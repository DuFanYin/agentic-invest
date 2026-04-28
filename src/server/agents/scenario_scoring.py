"""Scenario scoring node — LLM generates scenarios, Python normalises probabilities."""

from __future__ import annotations

import json
import logging

from src.server.models.analysis import FundamentalAnalysis, MacroAnalysis, MarketSentiment
from src.server.models.scenario import Scenario
from src.server.models.state import ResearchState
from src.server.services.openrouter import OpenRouterClient
from src.server.utils.contract import NODE_CONTRACTS, assert_writes
from src.server.utils.status import update_status

_READS  = NODE_CONTRACTS["scenario_scoring"].reads
_WRITES = NODE_CONTRACTS["scenario_scoring"].writes

logger = logging.getLogger(__name__)

_default_llm = OpenRouterClient()
_NODE = "scenario_scoring"

_MIN_SCENARIOS = 3
_MAX_SCENARIOS = 5

_SYSTEM = (
    "You are an investment strategist. "
    "Given the analysis below, generate distinct future scenarios with probability weights. "
    "Return only valid JSON — no markdown, no prose outside the JSON."
)

_SCHEMA = """
Return a JSON object with exactly one key "scenarios" containing an array of 3–5 scenario objects.
{
  "scenarios": [
    {
      "name": "...",
      "description": "...",
      "raw_probability": 0.4,
      "drivers": ["..."],
      "triggers": ["..."],
      "evidence_ids": ["ev_001", ...],
      "tags": ["bullish-2", "rate-sensitive"]
    },
    ...
  ]
}
Rules:
- name: descriptive of the future state, not bull/bear labels.
- raw_probability: estimated weight, need not sum to 1 — Python normalises.
- drivers: structural forces that make this scenario possible (2-4 items).
- triggers: specific events that would cause this scenario to play out (1-3 items).
- tags (required, at least 1): must include exactly one magnitude tag from:
    bearish-3, bearish-2, bearish-1, neutral, bullish-1, bullish-2, bullish-3
  plus any relevant domain labels (e.g. "policy-risk", "rate-sensitive").
- evidence_ids: cite at least one ID from the AVAILABLE EVIDENCE IDs list provided.
- Scenarios must represent meaningfully different causal paths.
"""


def _build_prompt(fundamental_analysis, macro_analysis, market_sentiment, evidence, intent, research_focus=None, plan_notes=None) -> str:
    evidence_ids = ", ".join(ev.id for ev in evidence) if evidence else "none"

    if isinstance(fundamental_analysis, FundamentalAnalysis):
        fa_claims = "\n".join(
            f"- {c.statement} (confidence: {c.confidence})"
            for c in fundamental_analysis.claims
        ) or "No fundamental claims available."
        fa_view = fundamental_analysis.business_quality.view
        fa_val = fundamental_analysis.valuation.relative_multiple_view
    else:
        fa_claims = "No fundamental claims available."
        fa_view = "unknown"
        fa_val = "unknown"

    if isinstance(macro_analysis, MacroAnalysis):
        macro_view = macro_analysis.macro_view
        macro_rate = macro_analysis.rate_environment
        macro_growth = macro_analysis.growth_environment
        macro_drivers = "\n".join(f"- {d}" for d in macro_analysis.macro_drivers) or "none"
    else:
        macro_view = "unknown"
        macro_rate = "unknown"
        macro_growth = "unknown"
        macro_drivers = "none"

    if isinstance(market_sentiment, MarketSentiment):
        ms_direction = market_sentiment.news_sentiment.direction
        ms_narrative = market_sentiment.market_narrative.summary
    else:
        ms_direction = "unknown"
        ms_narrative = ""

    horizon = intent.time_horizon if intent else "unspecified"
    ticker = intent.ticker if intent else "unknown"

    focus_str = "\n".join(f"- {f}" for f in (research_focus or [])) or "General investment analysis"
    notes_str = "\n".join(f"- {n}" for n in (plan_notes or [])) or "none"

    return f"""{_SCHEMA}

AVAILABLE EVIDENCE IDs: {evidence_ids}

TICKER: {ticker} | HORIZON: {horizon}

RESEARCH PLAN (scenarios must address these focus areas):
{focus_str}

Key questions the scenarios should resolve:
{notes_str}

FUNDAMENTAL ANALYSIS:
Business quality: {fa_view}
Valuation: {fa_val}
Key claims:
{fa_claims}

MACRO ENVIRONMENT:
View: {macro_view}
Rate environment: {macro_rate}
Growth environment: {macro_growth}
Key drivers:
{macro_drivers}

MARKET SENTIMENT:
Direction: {ms_direction}
Narrative: {ms_narrative}
"""


def _normalise(scenarios: list[Scenario]) -> list[Scenario]:
    total = sum(s.probability for s in scenarios) or 1.0
    return [
        s.model_copy(update={"probability": round(s.probability / total, 6)})
        for s in scenarios
    ]


def _parse_llm_scenarios(raw: str, evidence_ids: list[str]) -> list[Scenario]:
    data = json.loads(raw)
    # unwrap wrapper object {"scenarios": [...]} — required because json_object mode
    # cannot guarantee a top-level array; we always ask for the wrapper form.
    if isinstance(data, dict):
        data = data.get("scenarios", data)
    if not isinstance(data, list):
        raise ValueError(f"expected JSON array or {{\"scenarios\": [...]}}, got {type(data).__name__}")

    weights = [max(0.0, float(item.get("raw_probability", 0.0))) for item in data]
    total = sum(weights) or 1.0

    scenarios = []
    for i, (item, w) in enumerate(zip(data, weights)):
        tags = item.get("tags") or ["neutral"]
        if not isinstance(tags, list) or not tags:
            tags = ["neutral"]
        scenarios.append(Scenario(
            id=f"sc_{i + 1:03d}",
            name=item["name"],
            description=item["description"],
            probability=round(w / total, 6),
            drivers=item.get("drivers", []),
            triggers=item.get("triggers", []),
            evidence_ids=item.get("evidence_ids", evidence_ids[:1]),
            tags=tags,
        ))
    if len(scenarios) < _MIN_SCENARIOS or len(scenarios) > _MAX_SCENARIOS:
        raise ValueError(
            f"expected {_MIN_SCENARIOS}-{_MAX_SCENARIOS} scenarios, got {len(scenarios)}"
        )
    return scenarios


async def scenario_scoring_node(
    state: ResearchState, *, llm: OpenRouterClient = _default_llm
) -> ResearchState:

    evidence = state.get("evidence") or []
    fundamental_analysis = state.get("fundamental_analysis")
    macro_analysis = state.get("macro_analysis")
    market_sentiment = state.get("market_sentiment")
    intent = state.get("intent")
    plan_ctx = state.get("plan_context")
    research_focus: list[str] = plan_ctx.research_focus if plan_ctx else []
    plan_notes: list[str] = plan_ctx.plan_notes if plan_ctx else []
    statuses = list(state.get("agent_statuses") or [])
    statuses = update_status(
        statuses, "scenario_scoring",
        lifecycle="active", phase="scoring_scenarios", action="building scenarios",
    )

    evidence_ids = [ev.id for ev in evidence]
    scenarios: list[Scenario] | None = None
    llm_used = False

    if evidence:
        prompt = _build_prompt(fundamental_analysis, macro_analysis, market_sentiment, evidence, intent, research_focus, plan_notes)
        try:
            raw = await llm.call_with_retry(prompt, system=_SYSTEM, node=_NODE)
            parsed = _parse_llm_scenarios(raw, evidence_ids)
            scenarios = _normalise(parsed)
            llm_used = True
        except Exception as exc:
            logger.warning("%s: LLM step failed — %s", _NODE, exc)

    if scenarios is None:
        msg = f"[{_NODE}] unable to generate scenarios from LLM output"
        logger.error(msg)
        if statuses:
            statuses = update_status(
                statuses,
                "scenario_scoring",
                lifecycle="failed",
                phase="scoring_scenarios",
                action="scenario generation failed",
                last_error=msg,
            )
        raise RuntimeError(msg)

    # Sort by probability descending — most likely scenario first
    scenarios = sorted(scenarios, key=lambda s: s.probability, reverse=True)

    prob_sum = round(sum(s.probability for s in scenarios), 6)

    statuses = update_status(
        statuses, "scenario_scoring",
        lifecycle="standby", phase="scoring_scenarios", action="scenarios ready",
        details=[
            f"scenarios={len(scenarios)}",
            f"prob_sum={prob_sum}",
            f"llm={'yes' if llm_used else 'no'}",
        ],
    )
    statuses = update_status(
        statuses, "scenario_debate",
        lifecycle="active", phase="debating_scenarios", action="starting debate",
    )

    delta = {"scenarios": scenarios, "agent_statuses": statuses}
    assert_writes(delta, _WRITES, "scenario_scoring")
    return delta
