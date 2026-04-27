"""Fundamental analysis node — LLM-grounded claims over real evidence and metrics."""

from __future__ import annotations

import json
import logging

from src.server.models.analysis import FundamentalAnalysis
from src.server.models.state import ResearchState
from src.server.services.openrouter import OpenRouterClient
from src.server.utils.status import update_status

logger = logging.getLogger(__name__)

_default_llm = OpenRouterClient()
_NODE = "fundamental_analysis"

_SYSTEM = (
    "You are a senior equity analyst. "
    "Analyse the provided evidence and financial metrics and return a JSON object. "
    "Return only valid JSON — no markdown, no prose outside the JSON."
)

_SCHEMA = """
Return exactly this JSON structure (no extra keys):
{
  "claims": [
    { "statement": "...", "confidence": "high|medium|low", "evidence_ids": ["ev_001", ...] }
  ],
  "business_quality": { "view": "strong|stable|weak|deteriorating", "drivers": ["..."] },
  "financials": { "profitability_trend": "...", "cash_flow_quality": "..." },
  "valuation": { "relative_multiple_view": "...", "simplified_dcf_view": "..." },
  "fundamental_risks": [
    { "name": "...", "impact": "high|medium|low", "signal": "...", "evidence_ids": ["ev_001", ...] }
  ],
  "missing_fields": ["..."]
}
Rules:
- Every claim and risk must cite at least one evidence_id from the list provided.
- missing_fields: list any important data points absent from the evidence.
- Provide 2-4 claims and 1-3 risks.
"""


def _build_prompt(evidence, metrics, missing_fields, intent, research_focus, must_have_metrics, plan_notes) -> str:
    financial_evidence = [ev for ev in evidence if ev.source_type == "financial_api"]
    supplemental = [ev for ev in evidence if ev.source_type != "financial_api"]

    ev_lines = "\n".join(
        f"[{ev.id}] (reliability={ev.reliability}) {ev.summary}"
        for ev in financial_evidence
    ) or "No financial API evidence available."

    supplemental_lines = "\n".join(
        f"[{ev.id}] ({ev.source_type}) {ev.summary}"
        for ev in supplemental
    )[:2000]  # cap to avoid token bloat

    metrics_json = json.dumps(metrics, indent=2) if metrics else "{}"

    intent_str = ""
    if intent:
        intent_str = (
            f"Ticker: {intent.ticker or 'unknown'} | "
            f"Scope: {intent.scope} | "
            f"Horizon: {intent.time_horizon or 'unspecified'}"
        )

    focus_str = "\n".join(f"- {f}" for f in research_focus) if research_focus else "General fundamental analysis"
    metrics_str = ", ".join(must_have_metrics) if must_have_metrics else "standard financials"
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
{supplemental_lines or 'none'}

MISSING DATA: {', '.join(missing_fields) if missing_fields else 'none reported'}
"""

async def fundamental_analysis_node(
    state: ResearchState, *, llm: OpenRouterClient = _default_llm
) -> ResearchState:
    evidence = state.get("evidence") or []
    normalized_data = state.get("normalized_data")
    intent = state.get("intent")
    research_focus: list[str] = state.get("research_focus") or []
    must_have_metrics: list[str] = state.get("must_have_metrics") or []
    plan_notes: list[str] = state.get("plan_notes") or []
    statuses = list(state.get("agent_statuses") or [])
    if statuses:
        statuses = update_status(
            statuses, "fundamental_analysis",
            lifecycle="active", phase="analyzing_fundamentals", action="building analysis",
        )

    metrics = normalized_data.metrics.model_dump() if normalized_data else {}
    missing_fields = normalized_data.missing_fields if normalized_data else []

    result: FundamentalAnalysis | None = None

    if evidence:
        prompt = _build_prompt(evidence, metrics, missing_fields, intent, research_focus, must_have_metrics, plan_notes)
        try:
            raw = await llm.call_with_retry(prompt, system=_SYSTEM, node=_NODE)
            parsed = json.loads(raw)
            parsed["metrics"] = metrics
            parsed.setdefault("missing_fields", missing_fields)
            result = FundamentalAnalysis.model_validate(parsed)
        except Exception as exc:
            logger.warning("%s: LLM step failed — %s", _NODE, exc)

    if result is None:
        msg = f"[{_NODE}] unable to generate grounded fundamental analysis from LLM output"
        logger.error(msg)
        if statuses:
            statuses = update_status(
                statuses,
                "fundamental_analysis",
                lifecycle="failed",
                phase="analyzing_fundamentals",
                action="fundamental analysis failed",
                last_error=msg,
            )
        raise RuntimeError(msg)

    # Surface missing fields as open questions so retry gate has agent-sourced signal
    agent_questions: list[str] = [
        f"fundamental_analysis needs: {f}" for f in result.missing_fields
    ]

    if statuses:
        statuses = update_status(
            statuses, "fundamental_analysis",
            lifecycle="standby", phase="analyzing_fundamentals", action="fundamentals ready",
            details=[
                f"claims={len(result.claims)}",
                f"questions={len(agent_questions)}",
            ],
        )
        statuses = update_status(
            statuses, "retry_gate",
            lifecycle="active", phase="evaluating_gaps", action="checking for gaps",
        )

    return {
        "fundamental_analysis": result,
        "agent_statuses": statuses,
        "agent_questions": agent_questions,
    }
