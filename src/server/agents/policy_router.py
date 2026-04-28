"""Policy router node — applies deterministic rules to llm_judge's PolicyDecision.

Replaces llm_judge_router (which was a pure routing function with no state writes).
As a full node it can write retry_scope to state before routing.

Flow:
  llm_judge_node  →  writes policy_decision (judge's hint)
  policy_router_node → builds PolicyInput, runs engine, overwrites policy_decision,
                        writes retry_questions / retry_reason / retry_scope
  policy_router_fn   → reads policy_decision.action → routing target string
"""

from __future__ import annotations

import logging

from src.server.agents.llm_judge import MAX_RESEARCH_ITERATIONS
from src.server.models.analysis import (
    FundamentalAnalysis,
    MacroAnalysis,
    MarketSentiment,
)
from src.server.models.state import ResearchState
from src.server.services.policy import PolicyInput, evaluate_policy
from src.server.utils.contract import NODE_CONTRACTS, assert_reads, assert_writes
from src.server.utils.status import update_status

_READS = NODE_CONTRACTS["policy_router"].reads
_WRITES = NODE_CONTRACTS["policy_router"].writes

_NODE = "policy_router"
logger = logging.getLogger(__name__)


def _evidence_counts(evidence: list) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ev in evidence:
        src = getattr(ev, "source_type", "unknown")
        counts[src] = counts.get(src, 0) + 1
    return counts


async def policy_router_node(state: ResearchState) -> ResearchState:
    assert_reads(state, _READS, _NODE)

    policy_decision = state.get("policy_decision")
    statuses = list(state.get("agent_statuses") or [])

    if policy_decision is None:
        # llm_judge didn't run or failed to write — safe fallthrough
        logger.warning(
            "%s: no policy_decision in state — defaulting to continue", _NODE
        )
        delta = {
            "retry_questions": [],
            "retry_reason": "none",
            "retry_scope": None,
            "agent_statuses": statuses,
        }
        assert_writes(delta, _WRITES, _NODE)
        return delta

    evidence = state.get("evidence") or []
    normalized_data = state.get("normalized_data")
    fa = state.get("fundamental_analysis")
    macro = state.get("macro_analysis")
    ms = state.get("market_sentiment")

    inp = PolicyInput(
        research_iteration=state.get("research_iteration", 1),
        evidence_counts=_evidence_counts(evidence),
        conflict_count=len(normalized_data.conflicts) if normalized_data else 0,
        missing_field_count=len(normalized_data.missing_fields)
        if normalized_data
        else 0,
        fa_degraded=isinstance(fa, FundamentalAnalysis) and fa.degraded,
        macro_degraded=isinstance(macro, MacroAnalysis) and macro.degraded,
        ms_degraded=isinstance(ms, MarketSentiment) and ms.degraded,
        judge_reason=policy_decision.reason_code,
        judge_retry_question=policy_decision.retry_question,
        max_iterations=MAX_RESEARCH_ITERATIONS,
    )

    final_decision = evaluate_policy(inp)

    will_retry = final_decision.action in (
        "retry_full_research",
        "retry_capability_only",
    )
    retry_scope = (
        final_decision.targets
        if final_decision.action == "retry_capability_only" and final_decision.targets
        else None
    )
    retry_questions = (
        [final_decision.retry_question]
        if will_retry and final_decision.retry_question
        else []
    )

    if statuses:
        statuses = update_status(
            statuses,
            _NODE,
            lifecycle="waiting" if will_retry else "standby",
            phase="gap_retry_required" if will_retry else "gap_resolved",
            action=f"routing: {final_decision.action}",
            details=[
                f"reason={final_decision.reason_code}",
                f"scope={retry_scope or 'full'}",
            ],
        )
        if will_retry:
            statuses = update_status(
                statuses,
                "research",
                lifecycle="active",
                phase="retrying_evidence",
                action="supplementary evidence collection",
            )
        else:
            statuses = update_status(
                statuses,
                "scenario_scoring",
                lifecycle="active",
                phase="scoring_scenarios",
                action="scoring scenarios",
            )

    delta = {
        "policy_decision": final_decision,
        "retry_questions": retry_questions,
        "retry_reason": final_decision.reason_code,
        "retry_scope": retry_scope,
        "agent_statuses": statuses,
    }
    assert_writes(delta, _WRITES, _NODE)
    return delta


def policy_router_fn(state: ResearchState) -> str:
    """Routing function: maps PolicyDecision.action to graph target."""
    decision = state.get("policy_decision")
    if decision and decision.action in ("retry_full_research", "retry_capability_only"):
        return "research"
    return "scenario_scoring"
