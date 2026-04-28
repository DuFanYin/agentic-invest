"""Macro analysis node — LLM evaluates macro environment impact on the research subject."""

from __future__ import annotations

import json
import logging

from src.server.models.analysis import MacroAnalysis
from src.server.models.state import ResearchState
from src.server.services.llm_provider import LLMClient
from src.server.utils.contract import NODE_CONTRACTS, assert_reads, assert_writes
from src.server.utils.status import update_status

_READS  = NODE_CONTRACTS["macro_analysis"].reads
_WRITES = NODE_CONTRACTS["macro_analysis"].writes

logger = logging.getLogger(__name__)

_default_llm = LLMClient()
_NODE = "macro_analysis"

_SYSTEM = (
    "You are a macro economist and market strategist writing for a sophisticated investor. "
    "Translate macro data into insight-driven statements that embed actual figures and rates. "
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
  "rate_environment": "tightening|easing|stable",
  "growth_environment": "expanding|contracting|stable",
  "missing_fields": ["..."]
}
Rules:
- macro_view: one sentence that embeds a key figure (e.g. "The Fed held rates at 5.25–5.5% for the fourth consecutive meeting as core PCE remains above 3%").
- macro_drivers: 2-4 drivers, each embedding the actual rate/level/change (e.g. "10-year yield at 4.6%, up 40bps in 30 days — compressing equity multiples").
- macro_risks: 1-3 risks. signal must be a specific threshold or event to watch (e.g. "CPI re-accelerating above 3.5%").
- rate_environment: exactly one of tightening|easing|stable.
- growth_environment: exactly one of expanding|contracting|stable.
- missing_fields: macro data absent from the evidence that would change the view. Short phrases only, max 5 words each.
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
    state: ResearchState, *, llm: LLMClient = _default_llm
) -> ResearchState:
    assert_reads(state, _READS, _NODE)

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
        logger.warning("%s: LLM exhausted — returning degraded result", _NODE)
        result = MacroAnalysis(
            macro_view="Macro analysis unavailable.",
            degraded=True,
        )
        if statuses:
            statuses = update_status(
                statuses, "macro_analysis",
                lifecycle="degraded", phase="analyzing_macro", action="macro analysis degraded",
            )

    if statuses:
        statuses = update_status(
            statuses, "macro_analysis",
            lifecycle="standby", phase="analyzing_macro", action="macro ready",
            details=[
                f"rate_env={result.rate_environment}",
                f"growth_env={result.growth_environment}",
                f"drivers={len(result.macro_drivers)}",
            ],
        )
        statuses = update_status(
            statuses, "llm_judge",
            lifecycle="active", phase="evaluating_gaps", action="checking for gaps",
        )

    delta = {
        "macro_analysis": result,
        "agent_statuses": statuses,
    }
    assert_writes(delta, _WRITES, "macro_analysis")
    return delta
