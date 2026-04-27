"""Scenario scoring node — LLM generates scenarios, Python normalises scores."""

from __future__ import annotations

import json
import logging

from src.server.models.scenario import Scenario
from src.server.models.state import ResearchState
from src.server.services.openrouter import OpenRouterClient
from src.server.utils.status import update_status

logger = logging.getLogger(__name__)

_llm = OpenRouterClient()

_MIN_SCENARIOS = 3

_SYSTEM = (
    "You are an investment strategist. "
    "Given the analysis below, generate investment scenarios with probability weights. "
    "Return only valid JSON — no markdown, no prose outside the JSON."
)

_SCHEMA = """
Return a JSON array of exactly 3 scenario objects (bull, base, bear — in that order):
[
  {
    "name": "Bull case",
    "description": "...",
    "raw_score": 0.3,
    "triggers": ["..."],
    "signals": ["..."],
    "evidence_ids": ["ev_001", ...]
  },
  ...
]
Rules:
- raw_score is your estimated probability weight (need not sum to 1 — Python will normalise).
- Each scenario must cite at least one evidence_id from the list provided.
- triggers: conditions that would cause this scenario to play out.
- signals: observable early indicators to watch.
- Base case raw_score should typically be the highest.
"""


def _build_prompt(fundamental_analysis, market_sentiment, evidence, intent) -> str:
    evidence_ids = ", ".join(ev.id for ev in evidence) if evidence else "none"

    fa_claims = "\n".join(
        f"- {c['statement']} (confidence: {c.get('confidence','?')})"
        for c in fundamental_analysis.get("claims", [])
    ) or "No fundamental claims available."

    fa_view = fundamental_analysis.get("business_quality", {}).get("view", "unknown")
    fa_val = fundamental_analysis.get("valuation", {}).get("relative_multiple_view", "unknown")

    ms_direction = market_sentiment.get("news_sentiment", {}).get("direction", "unknown")
    ms_narrative = market_sentiment.get("market_narrative", {}).get("summary", "")

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
    total = sum(s.score for s in scenarios) or 1.0
    return [
        Scenario(
            name=s.name,
            description=s.description,
            score=round(s.score / total, 6),
            triggers=s.triggers,
            signals=s.signals,
            evidence_ids=s.evidence_ids,
        )
        for s in scenarios
    ]


def _pad_to_minimum(scenarios: list[Scenario], evidence_ids: list[str]) -> list[Scenario]:
    """Ensure at least _MIN_SCENARIOS exist; pad with an 'Other' scenario at weight 0."""
    defaults = [
        ("Bull case", "Upside scenario driven by stronger-than-expected results."),
        ("Base case", "Fundamentals evolve in line with current expectations."),
        ("Bear case", "Downside scenario driven by deteriorating conditions."),
    ]
    while len(scenarios) < _MIN_SCENARIOS:
        name, desc = defaults[len(scenarios)]
        scenarios.append(Scenario(
            name=name,
            description=desc,
            score=0.0,
            evidence_ids=evidence_ids[:1],
        ))
    return scenarios


def _parse_llm_scenarios(raw: str, evidence_ids: list[str]) -> list[Scenario]:
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("expected JSON array")
    # Collect raw weights first, then normalise before constructing Scenario
    # (Scenario.score has ge=0, le=1 so we can't store unnormalised values)
    items = []
    weights = []
    for item in data:
        items.append(item)
        weights.append(max(0.0, float(item.get("raw_score", 0.0))))

    total = sum(weights) or 1.0
    scenarios = []
    for item, w in zip(items, weights):
        scenarios.append(Scenario(
            name=item["name"],
            description=item["description"],
            score=round(w / total, 6),
            triggers=item.get("triggers", []),
            signals=item.get("signals", []),
            evidence_ids=item.get("evidence_ids", evidence_ids[:1]),
        ))
    return scenarios


def _fallback(evidence) -> list[Scenario]:
    evidence_ids = [ev.id for ev in evidence][:1]
    raw = [
        Scenario(name="Bull case", description="Upside scenario.", score=0.25, evidence_ids=evidence_ids),
        Scenario(name="Base case", description="Base scenario.", score=0.50, evidence_ids=evidence_ids),
        Scenario(name="Bear case", description="Downside scenario.", score=0.25, evidence_ids=evidence_ids),
    ]
    return _normalise(raw)


def scenario_scoring_node(state: ResearchState) -> ResearchState:
    evidence = state.get("evidence") or []
    fundamental_analysis = state.get("fundamental_analysis") or {}
    market_sentiment = state.get("market_sentiment") or {}
    intent = state.get("intent")
    statuses = list(state.get("agent_statuses") or [])

    evidence_ids = [ev.id for ev in evidence]
    scenarios: list[Scenario] | None = None
    llm_used = False

    if evidence:
        prompt = _build_prompt(fundamental_analysis, market_sentiment, evidence, intent)
        try:
            raw = _llm.call_with_retry(prompt, system=_SYSTEM)
            parsed = _parse_llm_scenarios(raw, evidence_ids)
            parsed = _pad_to_minimum(parsed, evidence_ids)
            scenarios = _normalise(parsed)
            llm_used = True
        except Exception as exc:
            logger.warning("scenario_scoring LLM failed: %s", exc)

    if scenarios is None:
        logger.warning("scenario_scoring falling back to stub output")
        scenarios = _fallback(evidence)

    score_sum = round(sum(s.score for s in scenarios), 6)

    if statuses:
        statuses = update_status(
            statuses, "scenario_scoring",
            status="completed", action="scenarios ready",
            details=[
                f"scenarios={len(scenarios)}",
                f"score_sum={score_sum}",
                f"llm={'yes' if llm_used else 'no'}",
            ],
        )
        statuses = update_status(
            statuses, "report_verification",
            status="running", action="generating report",
        )

    return {"scenarios": scenarios, "agent_statuses": statuses}
