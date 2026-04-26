"""Unit tests: scenario scoring node produces valid, normalised probabilities."""

from src.server.agents.scenario_scoring import scenario_scoring_node
from src.server.models.evidence import Evidence
from src.server.models.scenario import Scenario
from src.server.utils.validation import validate_scenario_scores


def _make_evidence() -> list[Evidence]:
    return [
        Evidence(
            id="ev_001",
            source_type="filing",
            title="Filing",
            url="https://example.com",
            published_at="2025-01-01T00:00:00Z",
            retrieved_at="2026-01-01T00:00:00Z",
            summary="dummy",
            reliability="high",
        )
    ]


def test_scenario_scores_sum_to_one() -> None:
    state = {
        "evidence": _make_evidence(),
        "fundamental_analysis": {"business_quality": {"view": "stable"}},
        "market_sentiment": {"news_sentiment": {"direction": "neutral_to_positive"}},
    }
    result = scenario_scoring_node(state)
    scenarios: list[Scenario] = result["scenarios"]

    assert validate_scenario_scores(scenarios) == []
    total = sum(s.score for s in scenarios)
    assert abs(total - 1.0) < 1e-6


def test_at_least_three_scenarios() -> None:
    state = {
        "evidence": _make_evidence(),
        "fundamental_analysis": {},
        "market_sentiment": {},
    }
    result = scenario_scoring_node(state)
    assert len(result["scenarios"]) >= 3
