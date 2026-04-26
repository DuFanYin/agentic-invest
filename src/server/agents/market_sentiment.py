from src.server.agents.research import ResearchResult


class MarketSentimentAgent:
    def run(self, research_result: ResearchResult) -> dict:
        evidence_ids = [evidence.id for evidence in research_result.evidence]
        return {
            "agent": "market_sentiment",
            "claims": [
                {
                    "statement": "Market narrative is constructive but still sensitive to expectation resets.",
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
