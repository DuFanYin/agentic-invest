"""Scenario scoring node."""

from src.server.models.scenario import Scenario
from src.server.models.state import ResearchState
from src.server.utils.status import update_status


def scenario_scoring_node(state: ResearchState) -> ResearchState:
    evidence = state.get("evidence") or []
    fundamental_analysis = state.get("fundamental_analysis") or {}
    market_sentiment = state.get("market_sentiment") or {}
    evidence_ids = [ev.id for ev in evidence]
    statuses = list(state.get("agent_statuses") or [])

    raw_scores = [0.25, 0.5, 0.25]
    if fundamental_analysis.get("business_quality", {}).get("view") == "stable":
        raw_scores = [0.28, 0.52, 0.2]
    if market_sentiment.get("news_sentiment", {}).get("direction") == "neutral_to_positive":
        raw_scores = [0.3, 0.5, 0.2]

    total = sum(raw_scores) or 1.0
    scores = [s / total for s in raw_scores]

    scenarios = [
        Scenario(
            name="Bull case",
            description="Demand surprise with stable margins and improving market sentiment.",
            score=scores[0],
            triggers=["Product mix improves", "Operating leverage materialises"],
            signals=["Revenue growth re-accelerates", "Gross margin expands"],
            evidence_ids=evidence_ids,
        ),
        Scenario(
            name="Base case",
            description="Fundamentals remain stable while market expectations normalise.",
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
        ),
    ]

    if statuses:
        statuses = update_status(
            statuses, "scenario_scoring",
            status="completed", action="scenarios ready",
            details=[f"scenarios={len(scenarios)}", f"score_sum={round(sum(s.score for s in scenarios), 6)}"],
        )
        statuses = update_status(
            statuses, "report_verification",
            status="running", action="generating report",
        )

    return {"scenarios": scenarios, "agent_statuses": statuses}
