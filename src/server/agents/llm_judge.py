"""LLM judge — two-stage retry decision before scenario generation.

Retries only when there is a concrete reason to believe new evidence can be obtained:
  - structural: intent lacks ticker (company scope cannot proceed without it)
  - analysis_robustness: LLM judge decides if the three analysis outputs are robust enough
  - evidence_conflict: LLM judge decides whether detected conflicts merit another research pass

LLM missing_fields (analysis wishlist) no longer trigger retries — they are recorded
in the report as data limitations, not routed back to research.
"""

from __future__ import annotations

import json

from src.server.models.state import ResearchState
from src.server.services.openrouter import OpenRouterClient
from src.server.utils.contract import NODE_CONTRACTS, assert_reads, assert_writes
from src.server.utils.status import update_status

_READS = NODE_CONTRACTS["llm_judge"].reads
_WRITES = NODE_CONTRACTS["llm_judge"].writes

_NODE = "llm_judge"
MAX_RESEARCH_ITERATIONS = 2


_SYSTEM_ANALYSIS_JUDGE = (
    "You are a conservative research-quality gate. "
    "Layer 1: decide if the three analyses (fundamental, macro, sentiment) are robust enough to proceed. "
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
    "Layer 2: decide whether the detected conflicts are serious enough that one more research pass is worth it. "
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


async def llm_judge_node(state: ResearchState, *, llm: OpenRouterClient | None = None) -> ResearchState:
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

    retry_question: str | None = None
    retry_reason: str = "none"

    if current_iteration < MAX_RESEARCH_ITERATIONS:
        scope = (intent.scope if intent else "").lower()

        if intent and scope == "company" and not intent.ticker:
            retry_question = "Need clearer company/ticker mapping from query context"
            retry_reason = "structural"

        elif evidence:
            llm = llm or OpenRouterClient()
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
                parsed = json.loads(raw)
                should_retry = bool(parsed.get("should_retry"))
                rq = str(parsed.get("retry_question") or "").strip()
                if should_retry and rq:
                    retry_question = rq
                    retry_reason = "analysis_robustness"
            except Exception:
                pass

        if retry_question is None and normalized_data and normalized_data.conflicts:
            try:
                llm = llm or OpenRouterClient()
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
                parsed = json.loads(raw)
                should_retry = bool(parsed.get("should_retry"))
                rq = str(parsed.get("retry_question") or "").strip()
                if should_retry and rq:
                    retry_question = rq
                    retry_reason = "evidence_conflict"
            except Exception:
                pass

    will_retry = retry_question is not None

    if statuses:
        statuses = update_status(
            statuses, "llm_judge",
            lifecycle="waiting" if will_retry else "standby",
            phase="gap_retry_required" if will_retry else "gap_resolved",
            action="retrying research" if will_retry else "gaps resolved",
            details=[f"reason={retry_reason}"],
        )
        if will_retry:
            statuses = update_status(
                statuses, "research",
                lifecycle="active", phase="retrying_evidence", action="supplementary evidence collection",
            )
        else:
            statuses = update_status(
                statuses, "scenario_scoring",
                lifecycle="active", phase="scoring_scenarios", action="scoring scenarios",
            )

    delta = {
        "retry_questions": [retry_question] if will_retry else [],
        "retry_reason": retry_reason,
        "agent_statuses": statuses,
    }
    assert_writes(delta, _WRITES, _NODE)
    return delta


def llm_judge_router(state: ResearchState) -> str:
    if state.get("retry_questions"):
        return "research"
    return "scenario_scoring"
