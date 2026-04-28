"""Market sentiment node — LLM synthesis over news evidence and price history."""

from __future__ import annotations

import json
import logging

from src.server.models.analysis import MarketNarrative, MarketSentiment, NewsSentiment
from src.server.models.state import ResearchState
from src.server.services.llm_provider import LLMClient
from src.server.utils.contract import NODE_CONTRACTS, assert_reads, assert_writes
from src.server.utils.status import update_status

_READS  = NODE_CONTRACTS["market_sentiment"].reads
_WRITES = NODE_CONTRACTS["market_sentiment"].writes

logger = logging.getLogger(__name__)

_default_llm = LLMClient()
_NODE = "market_sentiment"

_SYSTEM = (
    "You are a market analyst specialising in sentiment and price action. "
    "Synthesise news and price data into insight-driven statements that embed actual figures. "
    "Return only valid JSON — no markdown, no prose outside the JSON."
)

_SCHEMA = """
Return exactly this JSON structure (no extra keys):
{
  "claims": [
    { "statement": "...", "confidence": "high|medium|low", "evidence_ids": ["ev_001", ...] }
  ],
  "news_sentiment": { "direction": "positive|neutral|negative" },
  "price_action": { "return_30d_pct": 0.0, "volatility": "high|medium|low" },
  "market_narrative": { "summary": "..." },
  "sentiment_risks": [
    { "name": "...", "impact": "high|medium|low", "signal": "...", "evidence_ids": ["ev_001", ...] }
  ],
  "missing_fields": ["..."]
}
Rules:
- claims: 2-4 statements. Embed actual figures where available (e.g. "Stock fell 8% in 30 days on volume 2x the 90-day average, signalling institutional exit"). If price data is provided, at least one claim must reference return_30d_pct or volatility with the actual value.
- news_sentiment.direction: exactly one of positive|neutral|negative.
- news_sentiment.confidence: omit this field — not needed.
- price_action.return_30d_pct: numeric, positive = gain, negative = loss.
- market_narrative.summary: 1-2 sentences describing the dominant market story right now.
- sentiment_risks: 1-2 risks. signal must name a specific observable trigger.
- Every claim and sentiment_risk must cite at least one evidence_id.
- missing_fields: data you needed but lacked. Short phrases only, max 5 words each.
"""


def _build_prompt(news_evidence, price_history, all_evidence_ids) -> str:
    news_lines = "\n".join(
        f"[{ev.id}] {ev.summary}" for ev in news_evidence
    ) or "No news evidence available."

    price_str = json.dumps(price_history, indent=2) if price_history else "{}"

    ids_str = ", ".join(all_evidence_ids) if all_evidence_ids else "none"

    return f"""{_SCHEMA}

AVAILABLE EVIDENCE IDs: {ids_str}

NEWS HEADLINES:
{news_lines}

PRICE / MARKET DATA:
{price_str}
"""

async def market_sentiment_node(
    state: ResearchState, *, llm: LLMClient = _default_llm
) -> ResearchState:
    assert_reads(state, _READS, _NODE)

    evidence = state.get("evidence") or []
    normalized_data = state.get("normalized_data")
    statuses = list(state.get("agent_statuses") or [])
    if statuses:
        statuses = update_status(
            statuses, "market_sentiment",
            lifecycle="active", phase="analyzing_sentiment", action="building sentiment view",
        )

    news_evidence = [ev for ev in evidence if ev.source_type in ("news", "web")]
    all_evidence_ids = [ev.id for ev in evidence]
    price_history = normalized_data.metrics.price_history if normalized_data else {}

    result: MarketSentiment | None = None

    if evidence:
        prompt = _build_prompt(news_evidence, price_history, all_evidence_ids)
        try:
            raw = await llm.call_with_retry(prompt, system=_SYSTEM, node=_NODE)
            parsed = json.loads(raw)
            result = MarketSentiment.model_validate(parsed)
        except Exception as exc:
            logger.warning("%s: LLM step failed — %s", _NODE, exc)

    if result is None:
        logger.warning("%s: LLM exhausted — returning degraded result", _NODE)
        result = MarketSentiment(
            news_sentiment=NewsSentiment(direction="neutral"),
            market_narrative=MarketNarrative(summary="Sentiment analysis unavailable."),
            degraded=True,
        )
        if statuses:
            statuses = update_status(
                statuses,
                "market_sentiment",
                lifecycle="degraded",
                phase="analyzing_sentiment",
                action="sentiment analysis degraded",
            )

    if statuses:
        statuses = update_status(
            statuses, "market_sentiment",
            lifecycle="standby", phase="analyzing_sentiment", action="sentiment ready",
            details=[
                f"claims={len(result.claims)}",
                f"direction={result.news_sentiment.direction}",
            ],
        )
        statuses = update_status(
            statuses, "llm_judge",
            lifecycle="active", phase="evaluating_gaps", action="checking for gaps",
        )

    delta = {
        "market_sentiment": result,
        "agent_statuses": statuses,
    }
    assert_writes(delta, _WRITES, "market_sentiment")
    return delta
