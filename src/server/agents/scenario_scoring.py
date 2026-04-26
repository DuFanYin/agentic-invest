from src.server.agents.research import ResearchResult
from src.server.models.scenario import Scenario


class ScenarioScoringAgent:
    def run(
        self,
        research_result: ResearchResult,
        fundamental_analysis: dict,
        market_sentiment: dict,
    ) -> list[Scenario]:
        evidence_ids = [evidence.id for evidence in research_result.evidence]
        raw_scores = [0.25, 0.5, 0.25]
        if fundamental_analysis.get("business_quality", {}).get("view") == "stable":
            raw_scores = [0.28, 0.52, 0.2]
        if market_sentiment.get("news_sentiment", {}).get("direction") == "neutral_to_positive":
            raw_scores = [0.3, 0.5, 0.2]
        total = sum(raw_scores) or 1.0
        scores = [score / total for score in raw_scores]

        return [
            Scenario(
                name="Bull case",
                description="Demand surprise with stable margins and improving market sentiment.",
                score=scores[0],
                triggers=["Product mix improves", "Operating leverage materializes"],
                signals=["Revenue growth re-accelerates", "Gross margin expands"],
                evidence_ids=evidence_ids,
            ),
            Scenario(
                name="Base case",
                description="Fundamentals remain stable while market expectations normalize.",
                score=scores[1],
                triggers=["Demand remains steady", "Cost control remains effective"],
                signals=["Growth near long-term average", "Margin remains stable"],
                evidence_ids=evidence_ids,
            ),
            Scenario(
                name="Bear case",
                description="Fundamentals weaken or sentiment de-rates on lower confidence.",
                score=scores[2],
                triggers=["Industry demand softens", "Competition intensifies"],
                signals=["Revenue misses estimates", "Multiple compresses"],
                evidence_ids=evidence_ids,
            )
        ]
