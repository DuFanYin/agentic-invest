"""Scenario debate node — single-LLM bull/bear/judge calibration of scenario probabilities."""

from __future__ import annotations

import json
import logging

from src.server.models.analysis import (
    FundamentalAnalysis,
    MacroAnalysis,
    MarketSentiment,
    ProbabilityAdjustment,
    ScenarioDebate,
)
from src.server.models.scenario import Scenario
from src.server.models.state import ResearchState
from src.server.services.openrouter import OpenRouterClient
from src.server.utils.status import update_status

logger = logging.getLogger(__name__)

_default_llm = OpenRouterClient()
_NODE = "scenario_debate"

_SYSTEM = (
    "You are a senior investment committee moderator running a structured debate. "
    "Given scenarios with initial probabilities, debate and calibrate them using bull, bear, and judge perspectives. "
    "Return only valid JSON — no markdown, no prose outside the JSON."
)

_SCHEMA = """
Return exactly this JSON structure (no extra keys):
{
  "debate_summary": "...",
  "probability_adjustments": [
    {
      "scenario_name": "...",
      "before": 0.40,
      "after": 0.35,
      "delta": -0.05,
      "reason": "...",
      "evidence_refs": ["ev_001", "..."]
    }
  ],
  "calibrated_scenarios": [
    {
      "name": "...",
      "probability": 0.35,
      "tags": ["bearish-1"]
    }
  ],
  "confidence": "high|medium|low",
  "debate_flags": []
}
Rules:
- debate_summary: 1-2 sentences summarising the key debate insight and outcome.
- probability_adjustments: one entry per scenario that was adjusted. Omit scenarios with no change.
  delta = after - before. Each adjustment must have a reason and at least one evidence_ref.
- calibrated_scenarios: list ALL scenarios (adjusted and unadjusted) with final probabilities.
  Probabilities MUST sum to 1.0 exactly.
- Each probability must be in [0, 1].
- No single scenario probability may change by more than 0.15 from its initial value.
- confidence: your confidence in the calibration quality.
- debate_flags: include "fallback_to_baseline" if calibration failed and you returned initial probs.

DEBATE STRUCTURE (apply internally before answering):
BULL perspective: Which scenarios should have higher probability and why?
BEAR perspective: Which scenarios should have lower probability and why?
JUDGE ruling: Weigh both sides, apply constraints, output final calibrated probabilities.
"""


def _build_prompt(scenarios, fa, macro, sentiment, evidence) -> str:
    scenario_lines = "\n".join(
        f"  {i+1}. {s.name}: {s.probability:.3f} — {s.description[:120]}"
        for i, s in enumerate(scenarios)
    )

    evidence_ids = ", ".join(ev.id for ev in evidence) if evidence else "none"

    fa_summary = ""
    if isinstance(fa, FundamentalAnalysis):
        fa_summary = (
            f"Business quality: {fa.business_quality.view} | "
            f"Valuation: {fa.valuation.relative_multiple_view}"
        )

    macro_summary = ""
    if isinstance(macro, MacroAnalysis):
        macro_summary = (
            f"{macro.macro_view} | "
            f"Rate: {macro.rate_environment} | "
            f"Growth: {macro.growth_environment}"
        )

    sentiment_summary = ""
    if isinstance(sentiment, MarketSentiment):
        sentiment_summary = (
            f"News direction: {sentiment.news_sentiment.direction} | "
            f"Narrative: {sentiment.market_narrative.summary[:150]}"
        )

    return f"""{_SCHEMA}

AVAILABLE EVIDENCE IDs: {evidence_ids}

INITIAL SCENARIOS (to calibrate):
{scenario_lines}

FUNDAMENTAL ANALYSIS: {fa_summary or 'not available'}

MACRO ENVIRONMENT: {macro_summary or 'not available'}

MARKET SENTIMENT: {sentiment_summary or 'not available'}
"""


def _fallback_debate(scenarios: list[Scenario]) -> ScenarioDebate:
    return ScenarioDebate(
        debate_summary="Debate calibration failed — returning baseline probabilities.",
        probability_adjustments=[],
        calibrated_scenarios=[
            {"name": s.name, "probability": s.probability, "tags": s.tags}
            for s in scenarios
        ],
        confidence="low",
        debate_flags=["fallback_to_baseline"],
    )


def _validate_and_fix(
    raw_debate: dict,
    scenarios: list[Scenario],
) -> ScenarioDebate:
    """Parse LLM output, enforce hard constraints, fall back on failure."""
    adjustments = []
    for adj in raw_debate.get("probability_adjustments", []):
        before = float(adj.get("before", 0))
        after = float(adj.get("after", 0))
        delta = after - before
        if abs(delta) > 0.15:
            after = before + (0.15 if delta > 0 else -0.15)
            after = max(0.0, min(1.0, after))
            delta = round(after - before, 6)
        adjustments.append(ProbabilityAdjustment(
            scenario_name=adj["scenario_name"],
            before=round(before, 6),
            after=round(after, 6),
            delta=round(delta, 6),
            reason=adj.get("reason", ""),
            evidence_refs=adj.get("evidence_refs", []),
        ))

    calibrated = raw_debate.get("calibrated_scenarios", [])
    baseline_by_name = {s.name: s for s in scenarios}
    calibrated_names = {
        str(item.get("name"))
        for item in calibrated
        if isinstance(item, dict) and item.get("name")
    }
    if calibrated_names != set(baseline_by_name):
        # Coverage is mandatory: calibrated list must include all original scenarios.
        return _fallback_debate(scenarios)

    total = sum(s.get("probability", 0) for s in calibrated) or 1.0
    if abs(total - 1.0) > 0.01:
        # Normalise
        calibrated = [
            {**s, "probability": round(s.get("probability", 0) / total, 6)}
            for s in calibrated
        ]

    return ScenarioDebate(
        debate_summary=raw_debate.get("debate_summary", ""),
        probability_adjustments=adjustments,
        calibrated_scenarios=calibrated,
        confidence=raw_debate.get("confidence", "medium"),
        debate_flags=raw_debate.get("debate_flags", []),
    )


async def scenario_debate_node(
    state: ResearchState, *, llm: OpenRouterClient = _default_llm
) -> ResearchState:
    scenarios = state.get("scenarios") or []
    evidence = state.get("evidence") or []
    fa = state.get("fundamental_analysis")
    macro = state.get("macro_analysis")
    sentiment = state.get("market_sentiment")
    statuses = list(state.get("agent_statuses") or [])

    if statuses:
        statuses = update_status(
            statuses, "scenario_debate",
            lifecycle="active", phase="debating_scenarios", action="calibrating probabilities",
        )

    debate: ScenarioDebate | None = None

    if scenarios:
        prompt = _build_prompt(scenarios, fa, macro, sentiment, evidence)
        try:
            raw = await llm.call_with_retry(prompt, system=_SYSTEM, node=_NODE)
            parsed = json.loads(raw)
            debate = _validate_and_fix(parsed, scenarios)
        except Exception as exc:
            logger.warning("%s: LLM step failed — %s", _NODE, exc)
            debate = _fallback_debate(scenarios)
    else:
        debate = _fallback_debate([])

    if statuses:
        flags_str = ",".join(debate.debate_flags) if debate.debate_flags else "none"
        statuses = update_status(
            statuses, "scenario_debate",
            lifecycle="standby", phase="debating_scenarios", action="debate complete",
            details=[
                f"adjustments={len(debate.probability_adjustments)}",
                f"confidence={debate.confidence}",
                f"flags={flags_str}",
            ],
        )
        statuses = update_status(
            statuses, "report_finalize",
            lifecycle="active", phase="generating_report", action="generating report",
        )

    return {"scenario_debate": debate, "agent_statuses": statuses}
