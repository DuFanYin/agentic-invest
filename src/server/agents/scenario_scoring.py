"""Scenario scoring node — LLM generates scenarios, Python normalises probabilities."""

from __future__ import annotations

import json
import logging

from src.server.models.analysis import FundamentalAnalysis, MarketSentiment
from src.server.models.scenario import Scenario
from src.server.models.state import ResearchState
from src.server.services.openrouter import OpenRouterClient
from src.server.utils.status import update_status

logger = logging.getLogger(__name__)

_llm = OpenRouterClient()
_NODE = "scenario_scoring"

_MIN_SCENARIOS = 3
_MAX_SCENARIOS = 5

_SYSTEM = (
    "You are an investment strategist. "
    "Given the analysis below, generate distinct future scenarios with probability weights. "
    "Return only valid JSON — no markdown, no prose outside the JSON."
)

_SCHEMA = """
Return a JSON array of 3–5 scenario objects. Each scenario describes a distinct future state.
[
  {
    "name": "...",
    "description": "...",
    "raw_probability": 0.4,
    "drivers": ["..."],
    "triggers": ["..."],
    "signals": ["..."],
    "evidence_ids": ["ev_001", ...],
    "time_horizon": "...",
    "tags": ["bullish-2", "ai-demand", "rate-sensitive"]
  },
  ...
]
Rules:
- Scenarios are centered on what future state occurs, not on bull/bear framing.
  Use descriptive names: "AI capex supercycle", "Rate plateau stalls growth", not "Bull case".
- raw_probability: your estimated weight (need not sum to 1 — Python normalises).
- drivers: what structural forces make this scenario possible.
- triggers: specific events that would cause this scenario to play out.
- signals: observable early indicators to watch for.
- tags (required, at least 1): must include exactly one magnitude tag from:
    bearish-3, bearish-2, bearish-1, neutral, bullish-1, bullish-2, bullish-3
  plus any domain labels that apply, e.g. "ai-demand", "policy-risk", "rate-sensitive".
- Each scenario must cite at least one evidence_id from the list provided.
- Scenarios must represent meaningfully different causal paths, not just optimistic/pessimistic
  variants of the same story.
"""


def _build_prompt(fundamental_analysis, market_sentiment, evidence, intent) -> str:
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

    if isinstance(market_sentiment, MarketSentiment):
        ms_direction = market_sentiment.news_sentiment.direction
        ms_narrative = market_sentiment.market_narrative.summary
    else:
        ms_direction = "unknown"
        ms_narrative = ""

    horizon = intent.time_horizon if intent else "unspecified"
    ticker = intent.ticker if intent else "unknown"

    return f"""{_SCHEMA}

AVAILABLE EVIDENCE IDs: {evidence_ids}

TICKER: {ticker} | HORIZON: {horizon}

FUNDAMENTAL ANALYSIS:
Business quality: {fa_view}
Valuation: {fa_val}
Key claims:
{fa_claims}

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
    if not isinstance(data, list):
        raise ValueError("expected JSON array")

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
            signals=item.get("signals", []),
            evidence_ids=item.get("evidence_ids", evidence_ids[:1]),
            time_horizon=item.get("time_horizon"),
            tags=tags,
        ))
    if len(scenarios) < _MIN_SCENARIOS or len(scenarios) > _MAX_SCENARIOS:
        raise ValueError(
            f"expected {_MIN_SCENARIOS}-{_MAX_SCENARIOS} scenarios, got {len(scenarios)}"
        )
    return scenarios


def scenario_scoring_node(state: ResearchState) -> ResearchState:
    evidence = state.get("evidence") or []
    fundamental_analysis = state.get("fundamental_analysis")
    market_sentiment = state.get("market_sentiment")
    intent = state.get("intent")
    statuses = list(state.get("agent_statuses") or [])
    if statuses:
        statuses = update_status(
            statuses, "scenario_scoring",
            lifecycle="active", phase="scoring_scenarios", action="building scenarios",
        )

    evidence_ids = [ev.id for ev in evidence]
    scenarios: list[Scenario] | None = None
    llm_used = False

    if evidence:
        prompt = _build_prompt(fundamental_analysis, market_sentiment, evidence, intent)
        try:
            raw = _llm.call_with_retry(prompt, system=_SYSTEM)
            parsed = _parse_llm_scenarios(raw, evidence_ids)
            scenarios = _normalise(parsed)
            llm_used = True
        except Exception as exc:
            logger.warning("scenario_scoring LLM failed: %s", exc)

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

    if statuses:
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
            statuses, "report_verification",
            lifecycle="active", phase="generating_report", action="generating report",
        )

    return {"scenarios": scenarios, "agent_statuses": statuses}
