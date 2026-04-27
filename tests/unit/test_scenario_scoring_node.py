"""Unit tests for scenario_scoring_node — LLM injected directly."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

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


def _mock_llm(scenarios=None, raises: Exception | None = None, raw_array: bool = False):
    """Return LLM mock.  By default emits the wrapper object form {"scenarios": [...]}.
    Pass raw_array=True to simulate a model that ignores the prompt and returns a bare array."""
    llm = MagicMock()
    if raises:
        llm.call_with_retry = AsyncMock(side_effect=raises)
    elif raw_array:
        llm.call_with_retry = AsyncMock(return_value=json.dumps(scenarios or _llm_scenarios()))
    else:
        llm.call_with_retry = AsyncMock(return_value=json.dumps({"scenarios": scenarios or _llm_scenarios()}))
    return llm


def _run(coro):
    return asyncio.run(coro)


# ── output shape ───────────────────────────────────────────────────────────

def test_scenarios_shape_probability_and_sorting():
    result = _run(scenario_scoring_node(_state(), llm=_mock_llm()))
    scenarios = result["scenarios"]
    assert len(scenarios) >= 3
    total = sum(s.probability for s in scenarios)
    assert abs(total - 1.0) < 1e-5
    probs = [s.probability for s in scenarios]
    assert probs == sorted(probs, reverse=True)
    for s in scenarios:
        assert s.id
        assert s.name
        assert s.description
        assert len(s.evidence_ids) >= 1
        assert len(s.tags) >= 1


# ── probability normalisation ──────────────────────────────────────────────

def test_unnormalised_probabilities_are_normalised():
    result = _run(scenario_scoring_node(_state(), llm=_mock_llm(_llm_scenarios(probs=(3, 5, 2)))))
    total = sum(s.probability for s in result["scenarios"])
    assert abs(total - 1.0) < 1e-5


def test_equal_probabilities_normalise_to_thirds():
    result = _run(scenario_scoring_node(_state(), llm=_mock_llm(_llm_scenarios(probs=(1, 1, 1)))))
    for s in result["scenarios"]:
        assert abs(s.probability - 1 / 3) < 1e-5


# ── cardinality validation (3-5 required) ─────────────────────────────────

def test_raises_when_llm_returns_two_scenarios():
    with pytest.raises(RuntimeError, match="scenario_scoring"):
        _run(scenario_scoring_node(_state(), llm=_mock_llm(_llm_scenarios(probs=(0.6, 0.4))[:2])))


def test_bare_array_response_still_parsed():
    # Some models ignore the wrapper-object instruction and return a bare array;
    # _parse_llm_scenarios should handle both forms gracefully.
    result = _run(scenario_scoring_node(_state(), llm=_mock_llm(raw_array=True)))
    assert len(result["scenarios"]) >= 3


def test_raises_when_llm_returns_more_than_five_scenarios():
    six = _llm_scenarios(probs=(0.2, 0.2, 0.2)) + _llm_scenarios(probs=(0.2, 0.2, 0.0))
    with pytest.raises(RuntimeError, match="scenario_scoring"):
        _run(scenario_scoring_node(_state(), llm=_mock_llm(six)))


# ── raises on LLM failure (no stub fallback) ──────────────────────────────

def test_raises_when_llm_raises():
    with pytest.raises(RuntimeError, match="scenario_scoring"):
        _run(scenario_scoring_node(_state(), llm=_mock_llm(raises=RuntimeError("exhausted"))))


def test_raises_when_no_evidence():
    with pytest.raises(RuntimeError, match="scenario_scoring"):
        _run(scenario_scoring_node(_state(evidence=[]), llm=_mock_llm()))


# ── agent_statuses ─────────────────────────────────────────────────────────

def test_empty_statuses_returned_unchanged():
    result = _run(scenario_scoring_node(_state(), llm=_mock_llm()))
    assert result["agent_statuses"] == []
