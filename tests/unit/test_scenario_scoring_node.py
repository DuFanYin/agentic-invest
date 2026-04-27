"""Unit tests for scenario_scoring_node — LLM mocked."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.server.agents.scenario_scoring import scenario_scoring_node
from src.server.models.evidence import Evidence
from src.server.models.intent import ResearchIntent


def _evidence(n: int = 3) -> list[Evidence]:
    return [
        Evidence(
            id=f"ev_{i:03d}",
            source_type="financial_api",
            title=f"Evidence {i}",
            summary=f"Summary {i}",
            reliability="high",
            retrieved_at="2026-01-01T00:00:00Z",
        )
        for i in range(1, n + 1)
    ]


def _llm_scenarios(scores=(0.3, 0.5, 0.2)) -> list[dict]:
    names = ["Bull case", "Base case", "Bear case"]
    descs = ["Upside.", "Base.", "Downside."]
    return [
        {
            "name": names[i],
            "description": descs[i],
            "raw_score": scores[i],
            "triggers": ["trigger"],
            "signals": ["signal"],
            "evidence_ids": [f"ev_{i+1:03d}"],
        }
        for i in range(len(scores))
    ]


def _state(evidence=None, fa=None, ms=None):
    return {
        "query": "Analyse AAPL",
        "intent": ResearchIntent(ticker="AAPL", subjects=["Apple"], scope="company"),
        "evidence": evidence if evidence is not None else _evidence(),
        "fundamental_analysis": fa or {
            "claims": [{"statement": "Stable margins.", "confidence": "high", "evidence_ids": ["ev_001"]}],
            "business_quality": {"view": "stable"},
            "valuation": {"relative_multiple_view": "near median"},
        },
        "market_sentiment": ms or {
            "news_sentiment": {"direction": "positive"},
            "market_narrative": {"summary": "Optimistic."},
        },
        "agent_statuses": [],
    }


def _mock_llm(scenarios=None, raises: Exception | None = None):
    llm = MagicMock()
    if raises:
        llm.call_with_retry.side_effect = raises
    else:
        llm.call_with_retry.return_value = json.dumps(scenarios or _llm_scenarios())
    return llm


# ── output shape ───────────────────────────────────────────────────────────

def test_returns_scenarios_key():
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm()):
        result = scenario_scoring_node(_state())
    assert "scenarios" in result


def test_at_least_three_scenarios():
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm()):
        result = scenario_scoring_node(_state())
    assert len(result["scenarios"]) >= 3


def test_scores_sum_to_one():
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm()):
        result = scenario_scoring_node(_state())
    total = sum(s.score for s in result["scenarios"])
    assert abs(total - 1.0) < 1e-5


def test_all_scores_non_negative():
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm()):
        result = scenario_scoring_node(_state())
    assert all(s.score >= 0 for s in result["scenarios"])


def test_all_scenarios_have_evidence_ids():
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm()):
        result = scenario_scoring_node(_state())
    for s in result["scenarios"]:
        assert len(s.evidence_ids) >= 1


def test_all_scenarios_have_name_and_description():
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm()):
        result = scenario_scoring_node(_state())
    for s in result["scenarios"]:
        assert s.name
        assert s.description


# ── score normalisation ────────────────────────────────────────────────────

def test_unnormalised_scores_are_normalised():
    # raw_scores = [3, 5, 2] → normalised = [0.3, 0.5, 0.2]
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm(_llm_scenarios(scores=(3, 5, 2)))):
        result = scenario_scoring_node(_state())
    total = sum(s.score for s in result["scenarios"])
    assert abs(total - 1.0) < 1e-5
    # Bull should be 3/10 = 0.3
    assert abs(result["scenarios"][0].score - 0.3) < 1e-5


def test_equal_scores_normalise_to_thirds():
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm(_llm_scenarios(scores=(1, 1, 1)))):
        result = scenario_scoring_node(_state())
    for s in result["scenarios"]:
        assert abs(s.score - 1 / 3) < 1e-5


# ── padding when LLM returns fewer than 3 scenarios ───────────────────────

def test_padding_when_llm_returns_two_scenarios():
    two = _llm_scenarios(scores=(0.6, 0.4))[:2]
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm(two)):
        result = scenario_scoring_node(_state())
    assert len(result["scenarios"]) >= 3
    total = sum(s.score for s in result["scenarios"])
    assert abs(total - 1.0) < 1e-5


def test_padding_when_llm_returns_one_scenario():
    one = _llm_scenarios(scores=(1.0,))[:1]
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm(one)):
        result = scenario_scoring_node(_state())
    assert len(result["scenarios"]) >= 3


# ── fallback ───────────────────────────────────────────────────────────────

def test_fallback_when_llm_raises():
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm(raises=RuntimeError("exhausted"))):
        result = scenario_scoring_node(_state())
    assert len(result["scenarios"]) >= 3
    total = sum(s.score for s in result["scenarios"])
    assert abs(total - 1.0) < 1e-5


def test_fallback_when_no_evidence():
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm()):
        result = scenario_scoring_node(_state(evidence=[]))
    assert len(result["scenarios"]) >= 3


def test_fallback_scores_sum_to_one():
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm(raises=Exception("err"))):
        result = scenario_scoring_node(_state())
    total = sum(s.score for s in result["scenarios"])
    assert abs(total - 1.0) < 1e-5


# ── agent_statuses ─────────────────────────────────────────────────────────

def test_empty_statuses_returned_unchanged():
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm()):
        result = scenario_scoring_node(_state())
    assert result["agent_statuses"] == []
