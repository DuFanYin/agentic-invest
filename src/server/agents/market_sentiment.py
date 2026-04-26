"""Market sentiment node."""

from src.server.models.state import ResearchState
from src.server.utils.status import update_status


def market_sentiment_node(state: ResearchState) -> ResearchState:
    evidence = state.get("evidence") or []
    evidence_ids = [ev.id for ev in evidence]
    statuses = list(state.get("agent_statuses") or [])

    result = {
        "agent": "market_sentiment",
        "claims": [
            {
                "statement": "Market narrative is constructive but sensitive to expectation resets.",
                "confidence": "medium",
                "evidence_ids": evidence_ids[:2],
            },
            {
                "statement": "Short-term sentiment can diverge from long-term fundamentals.",
                "confidence": "medium",
                "evidence_ids": evidence_ids[1:],
            },
        ],
        "missing_fields": [],
        "news_sentiment": {
            "direction": "neutral_to_positive",
            "confidence": "medium",
        },
        "price_action": {
            "trend": "constructive",
            "volatility": "medium",
        },
        "market_narrative": {
            "summary": "Investors are focused on growth durability and execution quality.",
            "crowding_risk": "medium",
        },
        "sentiment_risks": [
            {
                "name": "Expectation reset",
                "impact": "medium",
                "signal": "negative revision cycle or weak guidance reaction",
                "evidence_ids": evidence_ids[:1],
            }
        ],
    }

    if statuses:
        statuses = update_status(
            statuses, "market_sentiment",
            status="completed", action="sentiment ready",
            details=[f"claims={len(result['claims'])}"],
        )
        statuses = update_status(
            statuses, "gap_check",
            status="running", action="checking for gaps",
        )

    return {"market_sentiment": result, "agent_statuses": statuses}
