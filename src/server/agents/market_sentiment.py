"""Market sentiment node — LLM synthesis over news evidence and price history."""

from __future__ import annotations

import json
import logging

from src.server.models.analysis import MarketNarrative, MarketSentiment, NewsSentiment
from src.server.models.state import ResearchState
from src.server.prompts import build_prompt
from src.server.services.llm_provider import LLMClient
from src.server.utils.contract import NODE_CONTRACTS, assert_reads, assert_writes
from src.server.utils.status import update_status

_READS = NODE_CONTRACTS["market_sentiment"].reads
_WRITES = NODE_CONTRACTS["market_sentiment"].writes

logger = logging.getLogger(__name__)

_default_llm = LLMClient()
_NODE = "market_sentiment"


def _build_prompt(news_evidence, price_history, all_evidence_ids) -> tuple[str, str]:
    news_lines = (
        "\n".join(f"[{ev.id}] {ev.summary}" for ev in news_evidence)
        or "No news evidence available."
    )

    price_str = json.dumps(price_history, indent=2) if price_history else "{}"

    ids_str = ", ".join(all_evidence_ids) if all_evidence_ids else "none"

    return build_prompt(
        "market_sentiment",
        "main",
        ids_str=ids_str,
        news_lines=news_lines,
        price_str=price_str,
    )


async def market_sentiment_node(
    state: ResearchState, *, llm: LLMClient = _default_llm
) -> ResearchState:
    assert_reads(state, _READS, _NODE)

    evidence = state.get("evidence") or []
    normalized_data = state.get("normalized_data")
    statuses = list(state.get("agent_statuses") or [])
    if statuses:
        statuses = update_status(
            statuses,
            "market_sentiment",
            lifecycle="active",
            phase="analyzing_sentiment",
            action="building sentiment view",
        )

    news_evidence = [ev for ev in evidence if ev.source_type in ("news", "web")]
    all_evidence_ids = [ev.id for ev in evidence]
    price_history = normalized_data.metrics.price_history if normalized_data else {}

    result: MarketSentiment | None = None

    if evidence:
        system, prompt = _build_prompt(news_evidence, price_history, all_evidence_ids)
        try:
            raw = await llm.call_with_retry(prompt, system=system, node=_NODE)
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
            statuses,
            "market_sentiment",
            lifecycle="standby",
            phase="analyzing_sentiment",
            action="sentiment ready",
            details=[
                f"claims={len(result.claims)}",
                f"direction={result.news_sentiment.direction}",
            ],
        )
        statuses = update_status(
            statuses,
            "llm_judge",
            lifecycle="active",
            phase="evaluating_readiness",
            action="reviewing analysis readiness",
        )

    delta = {
        "market_sentiment": result,
        "agent_statuses": statuses,
    }
    assert_writes(delta, _WRITES, "market_sentiment")
    return delta
