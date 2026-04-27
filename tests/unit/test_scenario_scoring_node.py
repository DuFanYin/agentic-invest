"""Unit tests for scenario_scoring_node — LLM mocked."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.server.agents.scenario_scoring import scenario_scoring_node
from src.server.models.analysis import FundamentalAnalysis, MarketSentiment
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


def _llm_scenarios(probs=(0.3, 0.5, 0.2)) -> list[dict]:
    names = ["Rate plateau stalls growth", "AI capex supercycle", "Regulatory crackdown"]
    descs = ["Headwinds persist.", "Tailwinds accelerate.", "Policy risk materialises."]
    tags = [["bearish-1", "rate-sensitive"], ["bullish-2", "ai-demand"], ["bearish-2", "policy-risk"]]
    return [
        {
            "name": names[i],
            "description": descs[i],
            "raw_probability": probs[i],
            "drivers": ["driver"],
            "triggers": ["trigger"],
            "signals": ["signal"],
            "evidence_ids": [f"ev_{i + 1:03d}"],
            "tags": tags[i],
        }
        for i in range(len(probs))
    ]


def _default_fa() -> FundamentalAnalysis:
    return FundamentalAnalysis.model_validate({
        "claims": [{"statement": "Stable margins.", "confidence": "high", "evidence_ids": ["ev_001"]}],
        "business_quality": {"view": "stable", "drivers": []},
        "financials": {"profitability_trend": "stable", "cash_flow_quality": "high"},
        "valuation": {"relative_multiple_view": "near median", "simplified_dcf_view": "fair"},
        "fundamental_risks": [],
        "missing_fields": [],
    })


def _default_ms() -> MarketSentiment:
    return MarketSentiment.model_validate({
        "claims": [],
        "news_sentiment": {"direction": "positive", "confidence": "medium"},
        "market_narrative": {"summary": "Optimistic.", "crowding_risk": "low"},
        "sentiment_risks": [],
        "missing_fields": [],
    })


def _state(evidence=None, fa=None, ms=None):
    return {
        "query": "Analyse AAPL",
        "intent": ResearchIntent(ticker="AAPL", subjects=["Apple"], scope="company"),
        "evidence": evidence if evidence is not None else _evidence(),
        "fundamental_analysis": fa if fa is not None else _default_fa(),
        "market_sentiment": ms if ms is not None else _default_ms(),
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


def test_probabilities_sum_to_one():
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm()):
        result = scenario_scoring_node(_state())
    total = sum(s.probability for s in result["scenarios"])
    assert abs(total - 1.0) < 1e-5


def test_all_probabilities_non_negative():
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm()):
        result = scenario_scoring_node(_state())
    assert all(s.probability >= 0 for s in result["scenarios"])


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


def test_all_scenarios_have_non_empty_tags():
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm()):
        result = scenario_scoring_node(_state())
    for s in result["scenarios"]:
        assert len(s.tags) >= 1


def test_scenarios_sorted_by_probability_descending():
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm()):
        result = scenario_scoring_node(_state())
    probs = [s.probability for s in result["scenarios"]]
    assert probs == sorted(probs, reverse=True)


def test_each_scenario_has_id():
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm()):
        result = scenario_scoring_node(_state())
    for s in result["scenarios"]:
        assert s.id


# ── probability normalisation ──────────────────────────────────────────────

def test_unnormalised_probabilities_are_normalised():
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm(_llm_scenarios(probs=(3, 5, 2)))):
        result = scenario_scoring_node(_state())
    total = sum(s.probability for s in result["scenarios"])
    assert abs(total - 1.0) < 1e-5


def test_equal_probabilities_normalise_to_thirds():
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm(_llm_scenarios(probs=(1, 1, 1)))):
        result = scenario_scoring_node(_state())
    for s in result["scenarios"]:
        assert abs(s.probability - 1 / 3) < 1e-5


# ── padding when LLM returns fewer than 3 scenarios ───────────────────────

def test_padding_when_llm_returns_two_scenarios():
    two = _llm_scenarios(probs=(0.6, 0.4))[:2]
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm(two)):
        result = scenario_scoring_node(_state())
    assert len(result["scenarios"]) >= 3
    total = sum(s.probability for s in result["scenarios"])
    assert abs(total - 1.0) < 1e-5


def test_padding_when_llm_returns_one_scenario():
    one = _llm_scenarios(probs=(1.0,))[:1]
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm(one)):
        result = scenario_scoring_node(_state())
    assert len(result["scenarios"]) >= 3


# ── raises on LLM failure (no stub fallback) ──────────────────────────────

def test_raises_when_llm_raises():
    with pytest.raises(RuntimeError, match="scenario_scoring"):
        with patch("src.server.agents.scenario_scoring._llm", _mock_llm(raises=RuntimeError("exhausted"))):
            scenario_scoring_node(_state())


def test_raises_when_no_evidence():
    with pytest.raises(RuntimeError, match="scenario_scoring"):
        with patch("src.server.agents.scenario_scoring._llm", _mock_llm()):
            scenario_scoring_node(_state(evidence=[]))


# ── agent_statuses ─────────────────────────────────────────────────────────

def test_empty_statuses_returned_unchanged():
    with patch("src.server.agents.scenario_scoring._llm", _mock_llm()):
        result = scenario_scoring_node(_state())
    assert result["agent_statuses"] == []
