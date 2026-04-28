"""LLM judge — two-stage retry decision before scenario generation.

Retries only when there is a concrete reason to believe new evidence can be obtained:
  - structural: intent lacks ticker (company scope cannot proceed without it)
  - analysis_robustness: LLM judge decides if the three analysis outputs are robust enough
  - evidence_conflict: LLM judge decides whether detected conflicts merit another research pass

Outputs PolicyDecision to state. policy_router_node reads it, applies deterministic
rules, and decides the actual routing action + retry_scope.
"""

from __future__ import annotations

import json
import logging

from src.server.models.analysis import JudgeDecision
from src.server.models.state import ResearchState
from src.server.services.llm_provider import LLMClient
from src.server.services.policy import PolicyDecision
from src.server.utils.contract import NODE_CONTRACTS, assert_reads, assert_writes
from src.server.utils.status import update_status

_READS = NODE_CONTRACTS["llm_judge"].reads
_WRITES = NODE_CONTRACTS["llm_judge"].writes

_NODE = "llm_judge"
MAX_RESEARCH_ITERATIONS = 2


_SYSTEM_ANALYSIS_JUDGE = (
    "You are a conservative research-quality gate. "
    "Your only job is to decide if the evidence and analyses are good enough to proceed to scenario generation. "
    "You do NOT decide what to search for in detail — the research node handles tactical search planning. "
    "If a retry is needed, provide one concrete directional question; the research node will expand it into specific queries. "
    "Bias toward proceeding unless there is a clear, material gap that another research pass is likely to fix. "
    "Return only valid JSON, no markdown, no extra keys."
)

_ANALYSIS_JUDGE_SCHEMA = """Return exactly this JSON (no extra keys):
{
  "should_retry": true,
  "retry_question": "one concrete web/news search directive, <= 20 words",
  "reason": "short reason, <= 12 words"
}
Rules:
- Default to should_retry=false.
- should_retry=true only if analyses are clearly not robust enough, the missing support is material, AND more evidence is likely obtainable in one more pass.
- Do not retry for minor thinness, normal uncertainty, or issues that report caveats can handle.
- retry_question must be an actionable search instruction (not generic).
- If should_retry=false, retry_question="".
"""

_SYSTEM_CONFLICT_JUDGE = (
    "You are a conservative evidence-conflict gate. "
    "Your only job is to decide whether detected conflicts are serious enough to warrant another research pass. "
    "You do NOT resolve the conflict yourself — provide one directional question pointing at the conflict; "
    "the research node will generate targeted search queries to resolve it. "
    "Bias toward proceeding unless the conflict is material, decision-relevant, and likely resolvable with one more pass. "
    "Return only valid JSON, no markdown, no extra keys."
)

_CONFLICT_JUDGE_SCHEMA = """Return exactly this JSON (no extra keys):
{
  "should_retry": true,
  "retry_question": "one concrete search directive to resolve the conflict, <= 20 words",
  "reason": "short reason, <= 12 words"
}
Rules:
- Default to should_retry=false.
- should_retry=true only if the conflicts are material, central to the thesis, and likely resolvable with one more search pass.
- Do not retry for normal source disagreement, small differences in framing, or conflicts that can simply be disclosed in the report.
- retry_question must target the conflict directly and be actionable.
- If should_retry=false, retry_question="".
"""


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

    if current_iteration < MAX_RESEARCH_ITERATIONS:
        scope = (intent.scope if intent else "").lower()

        if intent and scope == "company" and not intent.ticker:
            judge_reason = "structural"
            judge_retry_question = "Need clearer company/ticker mapping from query context"

        elif evidence:
            llm = llm or LLMClient()
            try:
                web_count = sum(1 for e in evidence if getattr(e, "source_type", "") == "web")
                news_count = sum(1 for e in evidence if getattr(e, "source_type", "") == "news")
                fin_count = sum(1 for e in evidence if getattr(e, "source_type", "") == "financial_api")
                macro_count = sum(1 for e in evidence if getattr(e, "source_type", "") == "macro_api")

                focus_lines = "\n".join(
                    f"- {f}" for f in (plan_ctx.research_focus if plan_ctx else [])[:3]
                ) or "none"
                must_metrics = ", ".join(
                    (plan_ctx.must_have_metrics if plan_ctx else [])[:6]
                ) or "none"

                def _claims_count(x: object) -> int:
                    return len(getattr(x, "claims", []) or [])

                def _missing_count(x: object) -> int:
                    return len(getattr(x, "missing_fields", []) or [])

                fa_present = fundamental is not None
                macro_present = macro is not None
                ms_present = sentiment is not None

                fa_claims = _claims_count(fundamental) if fa_present else 0
                ms_claims = _claims_count(sentiment) if ms_present else 0
                fa_missing = _missing_count(fundamental) if fa_present else 0
                macro_missing = _missing_count(macro) if macro_present else 0
                ms_missing = _missing_count(sentiment) if ms_present else 0

                judge_prompt = f"""{_ANALYSIS_JUDGE_SCHEMA}

SUBJECT: {subject}
HORIZON: {intent.time_horizon if intent else 'unspecified'}
SCOPE: {intent.scope if intent else 'unknown'}

EVIDENCE COUNTS:
- financial_api: {fin_count}
- macro_api: {macro_count}
- news: {news_count}
- web: {web_count}
- total: {len(evidence)}

ANALYSIS PRESENCE / SIGNAL:
- fundamental_present: {fa_present} (claims={fa_claims}, missing_fields={fa_missing})
- macro_present: {macro_present} (missing_fields={macro_missing})
- sentiment_present: {ms_present} (claims={ms_claims}, missing_fields={ms_missing})

RESEARCH FOCUS:
{focus_lines}

MUST-HAVE METRICS:
{must_metrics}
"""
                raw = await llm.call_with_retry(
                    judge_prompt,
                    system=_SYSTEM_ANALYSIS_JUDGE,
                    node=_NODE,
                )
                decision = JudgeDecision.model_validate(json.loads(raw))
                if decision.should_retry and decision.retry_question:
                    judge_retry_question = decision.retry_question
                    judge_reason = "analysis_robustness"
            except Exception as exc:
                logging.getLogger(__name__).warning("%s: analysis judge LLM failed — %s", _NODE, exc)
                judge_reason = "judge_degraded"

        if not judge_retry_question and normalized_data and normalized_data.conflicts:
            try:
                llm = llm or LLMClient()
                topics = ", ".join(
                    getattr(c, "topic", str(c)) for c in normalized_data.conflicts
                )
                conflict_lines = "\n".join(
                    f"- topic={getattr(c, 'topic', '')}; type={getattr(c, 'type', '')}; note={getattr(c, 'note', '')}"
                    for c in normalized_data.conflicts[:5]
                ) or "none"
                conflict_prompt = f"""{_CONFLICT_JUDGE_SCHEMA}

SUBJECT: {subject}
HORIZON: {intent.time_horizon if intent else 'unspecified'}
SCOPE: {intent.scope if intent else 'unknown'}

DETECTED CONFLICT TOPICS:
{topics}

CONFLICT DETAILS:
{conflict_lines}
"""
                raw = await llm.call_with_retry(
                    conflict_prompt,
                    system=_SYSTEM_CONFLICT_JUDGE,
                    node=_NODE,
                )
                decision = JudgeDecision.model_validate(json.loads(raw))
                if decision.should_retry and decision.retry_question:
                    judge_retry_question = decision.retry_question
                    judge_reason = "evidence_conflict"
            except Exception as exc:
                logging.getLogger(__name__).warning("%s: conflict judge LLM failed — %s", _NODE, exc)
                judge_reason = "judge_degraded"

    # Package LLM reasoning into PolicyDecision for policy_router to evaluate.
    # The action here is a hint — policy_router's rule engine makes the final call.
    _hint_action = (
        "retry_full_research" if judge_retry_question and judge_reason not in ("none", "judge_degraded")
        else "continue"
    )
    policy_decision = PolicyDecision(
        action=_hint_action,
        targets=[],
        retry_question=judge_retry_question,
        reason_code=judge_reason,
        rationale=f"judge reason: {judge_reason}",
    )

    is_degraded = judge_reason == "judge_degraded"
    will_hint_retry = bool(judge_retry_question)

    if statuses:
        statuses = update_status(
            statuses, "llm_judge",
            lifecycle="waiting" if will_hint_retry else ("degraded" if is_degraded else "standby"),
            phase="gap_retry_required" if will_hint_retry else "gap_resolved",
            action="retry hinted" if will_hint_retry else ("judge degraded" if is_degraded else "gaps resolved"),
            details=[f"reason={judge_reason}"],
        )

    delta = {
        "policy_decision": policy_decision,
        "agent_statuses": statuses,
    }
    assert_writes(delta, _WRITES, _NODE)
    return delta
