"""Unit tests: scenario scoring node produces valid, normalised probabilities."""

import json
from unittest.mock import MagicMock, patch

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


def _mock_llm():
    llm = MagicMock()
    llm.call_with_retry.return_value = json.dumps([
        {"name": "Bull case", "description": "Upside.", "raw_score": 0.3, "triggers": [], "signals": [], "evidence_ids": ["ev_001"]},
        {"name": "Base case", "description": "Base.", "raw_score": 0.5, "triggers": [], "signals": [], "evidence_ids": ["ev_001"]},
        {"name": "Bear case", "description": "Downside.", "raw_score": 0.2, "triggers": [], "signals": [], "evidence_ids": ["ev_001"]},
    ])
    return llm


def test_scenario_scores_sum_to_one() -> None:
    state = {
        "evidence": _make_evidence(),
        "fundamental_analysis": {"business_quality": {"view": "stable"}},
        "market_sentiment": {"news_sentiment": {"direction": "neutral_to_positive"}},
    }
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm()):
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
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm()):
        result = scenario_scoring_node(state)
    assert len(result["scenarios"]) >= 3
