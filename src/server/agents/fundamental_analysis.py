"""Fundamental analysis node — LLM-grounded claims over real evidence and metrics."""

from __future__ import annotations

import json
import logging

from src.server.models.state import ResearchState
from src.server.services.openrouter import OpenRouterClient
from src.server.utils.status import update_status

logger = logging.getLogger(__name__)

_llm = OpenRouterClient()

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


def _build_prompt(evidence, metrics, missing_fields, intent) -> str:
    ev_lines = "\n".join(
        f"[{ev.id}] ({ev.source_type}, reliability={ev.reliability}) {ev.summary}"
        for ev in evidence
    )

    metrics_json = json.dumps(metrics, indent=2) if metrics else "{}"

    intent_str = ""
    if intent:
        intent_str = (
            f"Ticker: {intent.ticker or 'unknown'} | "
            f"Scope: {intent.scope} | "
            f"Horizon: {intent.time_horizon or 'unspecified'}"
        )

    return f"""{_SCHEMA}

INTENT: {intent_str}

EVIDENCE:
{ev_lines}

FINANCIAL METRICS:
{metrics_json}

MISSING DATA: {', '.join(missing_fields) if missing_fields else 'none reported'}
"""


def _fallback(evidence, normalized_data) -> dict:
    evidence_ids = [ev.id for ev in evidence]
    return {
        "agent": "fundamental_analysis",
        "claims": [
            {
                "statement": "Insufficient data for a grounded fundamental view.",
                "confidence": "low",
                "evidence_ids": evidence_ids[:1],
            }
        ],
        "business_quality": {"view": "unknown", "drivers": []},
        "financials": {"profitability_trend": "unknown", "cash_flow_quality": "unknown"},
        "valuation": {"relative_multiple_view": "unknown", "simplified_dcf_view": "unknown"},
        "fundamental_risks": [],
        "missing_fields": normalized_data.get("missing_fields", []),
        "metrics": normalized_data.get("metrics", {}),
        "_llm_used": False,
    }


def fundamental_analysis_node(state: ResearchState) -> ResearchState:
    evidence = state.get("evidence") or []
    normalized_data = state.get("normalized_data") or {}
    intent = state.get("intent")
    statuses = list(state.get("agent_statuses") or [])

    metrics = normalized_data.get("metrics", {})
    missing_fields = normalized_data.get("missing_fields", [])

    result: dict | None = None

    if evidence:
        prompt = _build_prompt(evidence, metrics, missing_fields, intent)
        for attempt in range(2):
            try:
                raw = _llm.complete(prompt, system=_SYSTEM)
                parsed = json.loads(raw)
                # Attach metadata not returned by LLM
                parsed["agent"] = "fundamental_analysis"
                parsed["metrics"] = metrics
                parsed.setdefault("missing_fields", missing_fields)
                parsed["_llm_used"] = True
                result = parsed
                break
            except Exception as exc:
                logger.warning("fundamental_analysis LLM attempt %d failed: %s", attempt + 1, exc)

    if result is None:
        logger.warning("fundamental_analysis falling back to stub output")
        result = _fallback(evidence, normalized_data)

    if statuses:
        statuses = update_status(
            statuses, "fundamental_analysis",
            status="completed", action="fundamentals ready",
            details=[
                f"claims={len(result.get('claims', []))}",
                f"llm={'yes' if result.get('_llm_used') else 'no'}",
            ],
        )

    return {"fundamental_analysis": result, "agent_statuses": statuses}
