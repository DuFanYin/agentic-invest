"""Macro analysis node — LLM evaluates macro environment impact on the research subject."""

from __future__ import annotations

import json
import logging

from src.server.models.analysis import MacroAnalysis
from src.server.models.state import ResearchState
from src.server.services.openrouter import OpenRouterClient
from src.server.utils.status import update_status

logger = logging.getLogger(__name__)

_default_llm = OpenRouterClient()
_NODE = "macro_analysis"

_SYSTEM = (
    "You are a macro economist and market strategist. "
    "Analyse the provided macroeconomic data and return a JSON object assessing the macro environment. "
    "Return only valid JSON — no markdown, no prose outside the JSON."
)

_SCHEMA = """
Return exactly this JSON structure (no extra keys):
{
  "macro_view": "...",
  "macro_drivers": ["...", "..."],
  "macro_risks": [
    { "name": "...", "impact": "high|medium|low", "signal": "..." }
  ],
  "macro_signals": ["...", "..."],
  "rate_environment": "tightening|easing|stable",
  "growth_environment": "expanding|contracting|stable",
  "missing_fields": ["..."]
}
Rules:
- macro_view: one concise sentence summarising the current macro state.
- macro_drivers: 2-4 key macro forces and their direction (e.g. "Fed easing cycle underway", "Yield curve steepening").
- macro_risks: 1-3 risks. impact must be exactly one of: high, medium, low.
- macro_signals: 2-3 macro indicators to monitor going forward.
- rate_environment: classify overall monetary policy stance.
- growth_environment: classify overall economic growth trajectory.
- missing_fields: data points you wish you had but were not provided.
"""


def _build_prompt(macro_evidence, all_evidence, intent) -> str:
    macro_lines = "\n".join(
        f"[{ev.id}] {ev.summary}" for ev in macro_evidence
    ) or "No macro data available."

    supplemental_lines = "\n".join(
        f"[{ev.id}] ({ev.source_type}) {ev.summary}"
        for ev in all_evidence
        if ev.source_type not in ("macro_api",)
    )[:3000]  # cap to avoid token bloat

    intent_str = ""
    if intent:
        intent_str = (
            f"Research subject: {intent.subjects[0] if intent.subjects else 'unknown'} | "
            f"Ticker: {intent.ticker or 'N/A'} | "
            f"Scope: {intent.scope} | "
            f"Horizon: {intent.time_horizon or 'unspecified'}"
        )

    return f"""{_SCHEMA}

RESEARCH CONTEXT: {intent_str}

MACRO DATA (primary source):
{macro_lines}

SUPPLEMENTAL EVIDENCE (for context only):
{supplemental_lines or 'None'}
"""


async def macro_analysis_node(
    state: ResearchState, *, llm: OpenRouterClient = _default_llm
) -> ResearchState:
    evidence = state.get("evidence") or []
    intent = state.get("intent")
    statuses = list(state.get("agent_statuses") or [])
    if statuses:
        statuses = update_status(
            statuses, "macro_analysis",
            lifecycle="active", phase="analyzing_macro", action="building macro view",
        )

    macro_evidence = [ev for ev in evidence if ev.source_type == "macro_api"]

    result: MacroAnalysis | None = None

    if evidence:
        prompt = _build_prompt(macro_evidence, evidence, intent)
        try:
            raw = await llm.call_with_retry(prompt, system=_SYSTEM, node=_NODE)
            parsed = json.loads(raw)
            result = MacroAnalysis.model_validate(parsed)
        except Exception as exc:
            logger.warning("%s: LLM step failed — %s", _NODE, exc)

    if result is None:
        msg = f"[{_NODE}] unable to generate macro analysis from LLM output"
        logger.error(msg)
        if statuses:
            statuses = update_status(
                statuses, "macro_analysis",
                lifecycle="failed", phase="analyzing_macro", action="macro analysis failed",
                last_error=msg,
            )
        raise RuntimeError(msg)

    agent_questions: list[str] = [
        f"macro_analysis needs: {f}" for f in result.missing_fields
    ]

    if statuses:
        statuses = update_status(
            statuses, "macro_analysis",
            lifecycle="standby", phase="analyzing_macro", action="macro ready",
            details=[
                f"rate_env={result.rate_environment}",
                f"growth_env={result.growth_environment}",
                f"drivers={len(result.macro_drivers)}",
                f"questions={len(agent_questions)}",
            ],
        )
        statuses = update_status(
            statuses, "retry_gate",
            lifecycle="active", phase="evaluating_gaps", action="checking for gaps",
        )

    return {
        "macro_analysis": result,
        "agent_statuses": statuses,
        "agent_questions": agent_questions,
    }
