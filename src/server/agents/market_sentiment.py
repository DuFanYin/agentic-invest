"""Market sentiment node — LLM synthesis over news evidence and price history."""

from __future__ import annotations

import json
import logging

from src.server.models.state import ResearchState
from src.server.services.openrouter import OpenRouterClient
from src.server.utils.status import update_status

logger = logging.getLogger(__name__)

_llm = OpenRouterClient()
_NODE = "market_sentiment"

_SYSTEM = (
    "You are a market analyst specialising in sentiment and price action. "
    "Analyse the provided news headlines and price data and return a JSON object. "
    "Return only valid JSON — no markdown, no prose outside the JSON."
)

_SCHEMA = """
Return exactly this JSON structure (no extra keys):
{
  "claims": [
    { "statement": "...", "confidence": "high|medium|low", "evidence_ids": ["ev_001", ...] }
  ],
  "news_sentiment": { "direction": "positive|neutral|negative", "confidence": "high|medium|low" },
  "price_action": { "trend": "...", "return_30d_pct": 0.0, "volatility": "high|medium|low" },
  "market_narrative": { "summary": "...", "crowding_risk": "high|medium|low" },
  "sentiment_risks": [
    { "name": "...", "impact": "high|medium|low", "signal": "...", "evidence_ids": ["ev_001", ...] }
  ],
  "missing_fields": ["..."]
}
Rules:
- Every claim and sentiment_risk must cite at least one evidence_id from the list provided.
- news_sentiment.direction must be exactly one of: positive, neutral, negative.
- Provide 1-3 claims and 1-2 sentiment_risks.
- missing_fields: list data points you wish you had but were not provided.
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

def market_sentiment_node(state: ResearchState) -> ResearchState:
    evidence = state.get("evidence") or []
    normalized_data = state.get("normalized_data") or {}
    statuses = list(state.get("agent_statuses") or [])
    if statuses:
        statuses = update_status(
            statuses, "market_sentiment",
            lifecycle="active", phase="analyzing_sentiment", action="building sentiment view",
        )

    news_evidence = [ev for ev in evidence if ev.source_type in ("news", "web")]
    all_evidence_ids = [ev.id for ev in evidence]
    price_history = normalized_data.get("metrics", {}).get("price_history", {})

    result: dict | None = None

    if evidence:
        prompt = _build_prompt(news_evidence, price_history, all_evidence_ids)
        try:
            raw = _llm.call_with_retry(prompt, system=_SYSTEM)
            parsed = json.loads(raw)
            parsed["agent"] = "market_sentiment"
            parsed.setdefault("missing_fields", [])
            parsed["_llm_used"] = True
            result = parsed
        except Exception as exc:
            logger.warning("market_sentiment LLM failed: %s", exc)

    if result is None:
        msg = f"[{_NODE}] unable to generate grounded sentiment analysis from LLM output"
        logger.error(msg)
        if statuses:
            statuses = update_status(
                statuses,
                "market_sentiment",
                lifecycle="failed",
                phase="analyzing_sentiment",
                action="sentiment analysis failed",
                last_error=msg,
            )
        raise RuntimeError(msg)

    # Surface missing fields as open questions so gap_check has agent-sourced signal
    agent_questions: list[str] = []
    if result.get("_llm_used") and result.get("missing_fields"):
        agent_questions = [
            f"market_sentiment needs: {f}" for f in result["missing_fields"]
        ]

    if statuses:
        statuses = update_status(
            statuses, "market_sentiment",
            lifecycle="standby", phase="analyzing_sentiment", action="sentiment ready",
            details=[
                f"claims={len(result.get('claims', []))}",
                f"direction={result.get('news_sentiment', {}).get('direction', 'unknown')}",
                f"llm={'yes' if result.get('_llm_used') else 'no'}",
                f"questions={len(agent_questions)}",
            ],
        )
        statuses = update_status(
            statuses, "gap_check",
            lifecycle="active", phase="evaluating_gaps", action="checking for gaps",
        )

    return {
        "market_sentiment": result,
        "agent_statuses": statuses,
        "agent_questions": agent_questions,
    }
