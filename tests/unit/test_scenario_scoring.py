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
        {
            "name": "Downside scenario", "description": "Headwinds.",
            "disposition": "bearish-1", "raw_probability": 0.2,
            "drivers": ["margin pressure"], "triggers": ["rate hike"], "signals": ["guidance cut"],
            "evidence_ids": ["ev_001"], "tags": [],
        },
        {
            "name": "Base trajectory", "description": "In line.",
            "disposition": "neutral", "raw_probability": 0.5,
            "drivers": ["stable demand"], "triggers": ["earnings in-line"], "signals": ["flat guidance"],
            "evidence_ids": ["ev_001"], "tags": [],
        },
        {
            "name": "Upside scenario", "description": "Tailwinds.",
            "disposition": "bullish-1", "raw_probability": 0.3,
            "drivers": ["AI demand"], "triggers": ["beat estimates"], "signals": ["raised guidance"],
            "evidence_ids": ["ev_001"], "tags": [],
        },
    ])
    return llm


def test_scenario_probabilities_sum_to_one() -> None:
    state = {
        "evidence": _make_evidence(),
        "fundamental_analysis": None,
        "market_sentiment": None,
    }
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm()):
        result = scenario_scoring_node(state)
    scenarios: list[Scenario] = result["scenarios"]

    total = sum(s.probability for s in scenarios)
    assert abs(total - 1.0) < 1e-6


def test_at_least_three_scenarios() -> None:
    state = {
        "evidence": _make_evidence(),
        "fundamental_analysis": None,
        "market_sentiment": None,
    }
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm()):
        result = scenario_scoring_node(state)
    assert len(result["scenarios"]) >= 3


def test_validate_scenario_scores_passes_when_valid() -> None:
    scenarios = [
        Scenario(name="Down", description=".", probability=0.3, tags=["bearish-1"],
                 drivers=["d"], triggers=["t"], signals=["s"], evidence_ids=["ev_001"]),
        Scenario(name="Base", description=".", probability=0.5, tags=["neutral"],
                 drivers=["d"], triggers=["t"], signals=["s"], evidence_ids=["ev_001"]),
        Scenario(name="Up", description=".", probability=0.2, tags=["bullish-1"],
                 drivers=["d"], triggers=["t"], signals=["s"], evidence_ids=["ev_001"]),
    ]
    assert validate_scenario_scores(scenarios) == []


def test_validate_scenario_scores_fails_when_prob_wrong() -> None:
    scenarios = [
        Scenario(name="A", description=".", probability=0.5, tags=["neutral"],
                 drivers=["d"], triggers=["t"], signals=["s"], evidence_ids=["ev_001"]),
    ]
    errors = validate_scenario_scores(scenarios)
    assert any("sum" in e for e in errors)


def test_validate_scenario_scores_fails_when_missing_drivers() -> None:
    scenarios = [
        Scenario(name="A", description=".", probability=1.0, tags=["neutral"],
                 drivers=[], triggers=["t"], signals=["s"], evidence_ids=["ev_001"]),
    ]
    errors = validate_scenario_scores(scenarios)
    assert any("drivers" in e for e in errors)
