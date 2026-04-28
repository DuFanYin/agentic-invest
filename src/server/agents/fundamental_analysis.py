"""Fundamental analysis node — LLM-grounded claims over real evidence and metrics."""

from __future__ import annotations

import json
import logging

from src.server.models.analysis import BusinessQuality, FundamentalAnalysis, Valuation
from src.server.models.state import ResearchState
from src.server.services.llm_provider import LLMClient
from src.server.utils.contract import NODE_CONTRACTS, assert_reads, assert_writes
from src.server.utils.status import update_status

_READS = NODE_CONTRACTS["fundamental_analysis"].reads
_WRITES = NODE_CONTRACTS["fundamental_analysis"].writes

logger = logging.getLogger(__name__)

_default_llm = LLMClient()
_NODE = "fundamental_analysis"

_SYSTEM = (
    "You are a senior equity analyst writing for a sophisticated but non-specialist investor. "
    "Your job is to synthesise financial data into clear, insight-driven statements. "
    "Every claim must embed the actual numbers — do not separate data from interpretation. "
    "Return only valid JSON — no markdown, no prose outside the JSON."
)

_SCHEMA = """
Return exactly this JSON structure (no extra keys):
{
  "claims": [
    { "statement": "...", "confidence": "high|medium|low", "evidence_ids": ["ev_001", ...] }
  ],
  "business_quality": { "view": "strong|stable|weak|deteriorating" },
  "valuation": { "relative_multiple_view": "..." },
  "fundamental_risks": [
    { "name": "...", "impact": "high|medium|low", "signal": "...", "evidence_ids": ["ev_001", ...] }
  ],
  "missing_fields": ["..."]
}
Rules:
- claims: 3-5 statements. Each must embed specific numbers from the metrics (e.g. "Revenue grew 22% YoY to $44.1B"). Lead with the insight, embed the data inline. No claim without a number.
- business_quality.view: one of strong|stable|weak|deteriorating.
- valuation.relative_multiple_view: one sentence with the actual multiple (e.g. "Trades at 28x forward P/E, a 15% premium to sector median").
- fundamental_risks: 1-3 risks. signal must be a specific observable indicator, not a generic phrase.
- Every claim and risk must cite at least one evidence_id from the list provided.
- missing_fields: list data points absent from the evidence that would change the analysis. Short phrases only, max 5 words each.
"""


def _build_prompt(
    evidence,
    metrics,
    missing_fields,
    intent,
    research_focus,
    must_have_metrics,
    plan_notes,
) -> str:
    financial_evidence = [ev for ev in evidence if ev.source_type == "financial_api"]
    supplemental = [ev for ev in evidence if ev.source_type != "financial_api"]

    ev_lines = (
        "\n".join(
            f"[{ev.id}] (reliability={ev.reliability}) {ev.summary}"
            for ev in financial_evidence
        )
        or "No financial API evidence available."
    )

    supplemental_lines = "\n".join(
        f"[{ev.id}] ({ev.source_type}) {ev.summary}" for ev in supplemental
    )[:2000]  # cap to avoid token bloat

    metrics_json = json.dumps(metrics, indent=2) if metrics else "{}"

    intent_str = ""
    if intent:
        intent_str = (
            f"Ticker: {intent.ticker or 'unknown'} | "
            f"Scope: {intent.scope} | "
            f"Horizon: {intent.time_horizon or 'unspecified'}"
        )

    focus_str = (
        "\n".join(f"- {f}" for f in research_focus)
        if research_focus
        else "General fundamental analysis"
    )
    metrics_str = (
        ", ".join(must_have_metrics) if must_have_metrics else "standard financials"
    )
    notes_str = "\n".join(f"- {n}" for n in plan_notes) if plan_notes else "none"

    return f"""{_SCHEMA}

INTENT: {intent_str}

RESEARCH PLAN:
Focus areas:
{focus_str}

Must-have metrics: {metrics_str}

Specific questions to address:
{notes_str}

FINANCIAL API EVIDENCE (primary source):
{ev_lines}

FINANCIAL METRICS:
{metrics_json}

SUPPLEMENTAL EVIDENCE (macro/news — for context only, do not lead with these):
{supplemental_lines or "none"}

MISSING DATA: {", ".join(missing_fields) if missing_fields else "none reported"}
"""


async def fundamental_analysis_node(
    state: ResearchState, *, llm: LLMClient = _default_llm
) -> ResearchState:
    assert_reads(state, _READS, _NODE)

    evidence = state.get("evidence") or []
    normalized_data = state.get("normalized_data")
    intent = state.get("intent")
    plan_ctx = state.get("plan_context")
    research_focus: list[str] = plan_ctx.research_focus if plan_ctx else []
    must_have_metrics: list[str] = plan_ctx.must_have_metrics if plan_ctx else []
    plan_notes: list[str] = plan_ctx.plan_notes if plan_ctx else []
    statuses = list(state.get("agent_statuses") or [])
    if statuses:
        statuses = update_status(
            statuses,
            "fundamental_analysis",
            lifecycle="active",
            phase="analyzing_fundamentals",
            action="building analysis",
        )

    metrics = normalized_data.metrics.model_dump() if normalized_data else {}
    missing_fields = normalized_data.missing_fields if normalized_data else []

    result: FundamentalAnalysis | None = None

    if evidence:
        prompt = _build_prompt(
            evidence,
            metrics,
            missing_fields,
            intent,
            research_focus,
            must_have_metrics,
            plan_notes,
        )
        try:
            raw = await llm.call_with_retry(prompt, system=_SYSTEM, node=_NODE)
            parsed = json.loads(raw)
            parsed["metrics"] = metrics
            parsed.setdefault("missing_fields", missing_fields)
            result = FundamentalAnalysis.model_validate(parsed)
        except Exception as exc:
            logger.warning("%s: LLM step failed — %s", _NODE, exc)

    if result is None:
        logger.warning("%s: LLM exhausted — returning degraded result", _NODE)
        result = FundamentalAnalysis(
            claims=[],
            business_quality=BusinessQuality(view="stable"),
            valuation=Valuation(relative_multiple_view="unavailable"),
            degraded=True,
        )
        if statuses:
            statuses = update_status(
                statuses,
                "fundamental_analysis",
                lifecycle="degraded",
                phase="analyzing_fundamentals",
                action="fundamental analysis degraded",
            )

    if statuses:
        statuses = update_status(
            statuses,
            "fundamental_analysis",
            lifecycle="standby",
            phase="analyzing_fundamentals",
            action="fundamentals ready",
            details=[f"claims={len(result.claims)}"],
        )
        statuses = update_status(
            statuses,
            "llm_judge",
            lifecycle="active",
            phase="evaluating_gaps",
            action="checking for gaps",
        )

    delta = {
        "fundamental_analysis": result,
        "agent_statuses": statuses,
    }
    assert_writes(delta, _WRITES, "fundamental_analysis")
    return delta
