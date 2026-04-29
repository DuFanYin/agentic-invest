"""LLM judge — unified assess+decide stage before scenario generation.

Retries only when there is a concrete reason to believe new evidence can be obtained:
  - structural: intent lacks ticker (company scope cannot proceed without it)
  - analysis_robustness: LLM judge decides if the three analysis outputs are robust enough
  - evidence_conflict: LLM judge decides whether detected conflicts merit another research pass

When research_iteration >= MAX_RESEARCH_ITERATIONS, LLM calls are skipped and judge_reason is
set to "skipped_iteration_cap"; evaluate_policy still runs and rule_iteration_limit wins first.

This node does both:
  1) LLM assessment (should we retry? with what directional question?)
  2) Deterministic policy evaluation (final action + retry_scope)
"""

from __future__ import annotations

import json
import logging

from src.server.models.analysis import JudgeDecision
from src.server.models.state import ResearchState
from src.server.prompts import build_prompt, judge_strictness_guidance
from src.server.services.llm_provider import LLMClient
from src.server.services.policy import PolicyDecision, PolicyInput, evaluate_policy
from src.server.utils.contract import NODE_CONTRACTS, assert_reads, assert_writes
from src.server.utils.status import update_status

_READS = NODE_CONTRACTS["llm_judge"].reads
_WRITES = NODE_CONTRACTS["llm_judge"].writes

logger = logging.getLogger(__name__)

_NODE = "llm_judge"
MAX_RESEARCH_ITERATIONS = 3


async def _assess_analysis(
    *,
    subject: str,
    intent,
    evidence: list,
    plan_ctx,
    fundamental,
    macro,
    sentiment,
    llm: LLMClient,
    research_iteration: int,
    max_research_iterations: int,
) -> tuple[str, str]:
    """LLM call: is the analysis output robust enough? Returns (judge_reason, retry_question)."""
    web_count = sum(1 for e in evidence if getattr(e, "source_type", "") == "web")
    news_count = sum(1 for e in evidence if getattr(e, "source_type", "") == "news")
    fin_count = sum(1 for e in evidence if getattr(e, "source_type", "") == "financial_api")
    macro_count = sum(1 for e in evidence if getattr(e, "source_type", "") == "macro_api")

    focus_lines = "\n".join(f"- {f}" for f in (plan_ctx.research_focus if plan_ctx else [])[:3]) or "none"
    must_metrics = ", ".join((plan_ctx.must_have_metrics if plan_ctx else [])[:6]) or "none"

    def _claims_count(x: object) -> int:
        return len(getattr(x, "claims", []) or [])

    fa_present = fundamental is not None
    macro_present = macro is not None
    ms_present = sentiment is not None

    sg = judge_strictness_guidance(research_iteration, max_research_iterations)
    system_j, judge_prompt = build_prompt(
        "llm_judge",
        "analysis",
        subject=subject,
        horizon=intent.time_horizon if intent else "unspecified",
        scope=intent.scope if intent else "unknown",
        fin_count=fin_count,
        macro_count=macro_count,
        news_count=news_count,
        web_count=web_count,
        evidence_total=len(evidence),
        fa_present=fa_present,
        fa_claims=_claims_count(fundamental) if fa_present else 0,
        macro_present=macro_present,
        ms_present=ms_present,
        ms_claims=_claims_count(sentiment) if ms_present else 0,
        focus_lines=focus_lines,
        must_metrics=must_metrics,
        strictness_guidance=sg,
    )
    raw = await llm.call_with_retry(judge_prompt, system=system_j, node=_NODE)
    decision = JudgeDecision.model_validate(json.loads(raw))
    if decision.should_retry and decision.retry_question:
        return "analysis_robustness", decision.retry_question
    return "none", ""


async def _assess_conflict(
    *, subject: str, intent, conflicts: list, llm: LLMClient, research_iteration: int, max_research_iterations: int
) -> tuple[str, str]:
    """LLM call: do detected conflicts warrant re-research? Returns (judge_reason, retry_question)."""
    topics = ", ".join(getattr(c, "topic", str(c)) for c in conflicts)
    conflict_lines = (
        "\n".join(
            f"- topic={getattr(c, 'topic', '')}; type={getattr(c, 'type', '')}; note={getattr(c, 'note', '')}"
            for c in conflicts[:5]
        )
        or "none"
    )
    sg = judge_strictness_guidance(research_iteration, max_research_iterations)
    system_c, conflict_prompt = build_prompt(
        "llm_judge",
        "conflict",
        subject=subject,
        horizon=intent.time_horizon if intent else "unspecified",
        scope=intent.scope if intent else "unknown",
        topics=topics,
        conflict_lines=conflict_lines,
        strictness_guidance=sg,
    )
    raw = await llm.call_with_retry(conflict_prompt, system=system_c, node=_NODE)
    decision = JudgeDecision.model_validate(json.loads(raw))
    if decision.should_retry and decision.retry_question:
        return "evidence_conflict", decision.retry_question
    return "none", ""


async def llm_judge_node(state: ResearchState, *, llm: LLMClient | None = None) -> ResearchState:
    assert_reads(state, _READS, _NODE)

    intent = state.get("intent")
    normalized_data = state.get("normalized_data")
    evidence = state.get("evidence") or []
    plan_ctx = state.get("plan_context")
    fundamental = state.get("fundamental_analysis")
    macro = state.get("macro_analysis")
    sentiment = state.get("market_sentiment")
    current_iteration = state.get("research_iteration", 1)
    statuses = list(state.get("agent_statuses") or [])
    subject = (
        (intent.subjects[0] if intent and intent.subjects else None)
        or (intent.ticker if intent else None)
        or "the subject"
    )

    judge_reason: str = "none"
    judge_retry_question: str = ""

    if current_iteration >= MAX_RESEARCH_ITERATIONS:
        logger.info(
            "%s: research_iteration=%s >= cap=%s — skipping LLM judge; "
            'policy rule_iteration_limit applies (not "LLM said no issues").',
            _NODE,
            current_iteration,
            MAX_RESEARCH_ITERATIONS,
        )
        judge_reason = "skipped_iteration_cap"
    else:
        scope = (intent.scope if intent else "").lower()
        if intent and scope == "company" and not intent.ticker:
            judge_reason = "structural"
            judge_retry_question = "Need clearer company/ticker mapping from query context"
        elif evidence:
            llm = llm or LLMClient()
            try:
                judge_reason, judge_retry_question = await _assess_analysis(
                    subject=subject,
                    intent=intent,
                    evidence=evidence,
                    plan_ctx=plan_ctx,
                    fundamental=fundamental,
                    macro=macro,
                    sentiment=sentiment,
                    llm=llm,
                    research_iteration=current_iteration,
                    max_research_iterations=MAX_RESEARCH_ITERATIONS,
                )
            except Exception as exc:
                logger.warning("%s: analysis judge LLM failed — %s", _NODE, exc)
                judge_reason = "judge_degraded"

        if not judge_retry_question and normalized_data and normalized_data.conflicts:
            try:
                llm = llm or LLMClient()
                judge_reason, judge_retry_question = await _assess_conflict(
                    subject=subject,
                    intent=intent,
                    conflicts=normalized_data.conflicts,
                    llm=llm,
                    research_iteration=current_iteration,
                    max_research_iterations=MAX_RESEARCH_ITERATIONS,
                )
            except Exception as exc:
                logger.warning("%s: conflict judge LLM failed — %s", _NODE, exc)
                judge_reason = "judge_degraded"

    # Run deterministic policy rules using LLM reasoning as a hint.
    if judge_reason == "judge_degraded":
        policy_decision = PolicyDecision(
            action="continue",
            targets=[],
            retry_question="",
            reason_code="judge_degraded",
            rationale="judge degraded; proceeding without retry hint",
        )
    else:
        inp = PolicyInput(
            research_iteration=current_iteration,
            fa_degraded=bool(getattr(fundamental, "degraded", False)),
            macro_degraded=bool(getattr(macro, "degraded", False)),
            ms_degraded=bool(getattr(sentiment, "degraded", False)),
            judge_reason=judge_reason,
            judge_retry_question=judge_retry_question,
            max_iterations=MAX_RESEARCH_ITERATIONS,
        )
        policy_decision = evaluate_policy(inp)
    will_retry = policy_decision.action in ("retry_full_research", "retry_capability_only")
    retry_scope = (
        policy_decision.targets
        if policy_decision.action == "retry_capability_only" and policy_decision.targets
        else None
    )
    retry_questions = [policy_decision.retry_question] if will_retry and policy_decision.retry_question else []

    is_degraded = judge_reason == "judge_degraded"

    if statuses:
        statuses = update_status(
            statuses,
            "llm_judge",
            lifecycle="waiting" if will_retry else ("degraded" if is_degraded else "standby"),
            phase="retry_required" if will_retry else "ready_to_proceed",
            action="supplementary research suggested"
            if will_retry
            else ("judge degraded" if is_degraded else "ready to continue"),
            details=[
                f"reason={policy_decision.reason_code}",
                f"action={policy_decision.action}",
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
        elif policy_decision.action == "halt_with_degraded_output":
            statuses = update_status(
                statuses,
                "report_finalize",
                lifecycle="active",
                phase="generating_report",
                action="all analyses degraded — skipping scenario synthesis",
            )
        else:
            statuses = update_status(
                statuses, "scenario_scoring", lifecycle="active", phase="scoring_scenarios", action="scoring scenarios"
            )

    delta = {
        "policy_decision": policy_decision,
        "retry_questions": retry_questions,
        "retry_reason": policy_decision.reason_code,
        "retry_scope": retry_scope,
        "agent_statuses": statuses,
    }
    assert_writes(delta, _WRITES, _NODE)
    return delta


def llm_judge_router_fn(state: ResearchState) -> str:
    """Routing function: maps PolicyDecision.action to graph target."""
    decision = state.get("policy_decision")
    if decision and decision.action in ("retry_full_research", "retry_capability_only"):
        return "research"
    if decision and decision.action == "halt_with_degraded_output":
        return "report_finalize"
    return "scenario_scoring"
