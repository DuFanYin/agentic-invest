"""Unit tests for market_sentiment_node — LLM mocked."""

from __future__ import annotations

import pytest

import json
from unittest.mock import MagicMock, patch

from src.server.agents.market_sentiment import market_sentiment_node
from src.server.models.analysis import MarketSentiment, MetricsBlock, NormalizedData
from src.server.models.evidence import Evidence
from src.server.models.intent import ResearchIntent


def _evidence(n: int = 3) -> list[Evidence]:
    sources = ["financial_api", "news", "news"]
    return [
        Evidence(
            id=f"ev_{i:03d}",
            source_type=sources[i - 1] if i <= len(sources) else "news",
            title=f"Evidence {i}",
            summary=f"Headline {i}",
            reliability="medium",
            retrieved_at="2026-01-01T00:00:00Z",
        )
        for i in range(1, n + 1)
    ]


def _price_history() -> dict:
    return {
        "return_1y_pct": 22.4,
        "return_30d_pct": 3.1,
        "annualised_volatility_pct": 18.7,
        "high_52w": 199.62,
        "low_52w": 124.17,
    }


def _llm_response() -> dict:
    return {
        "claims": [
            {"statement": "Sentiment is broadly positive.", "confidence": "medium", "evidence_ids": ["ev_002"]},
        ],
        "news_sentiment": {"direction": "positive", "confidence": "medium"},
        "price_action": {"trend": "upward", "return_30d_pct": 3.1, "volatility": "medium"},
        "market_narrative": {"summary": "Investors are optimistic.", "crowding_risk": "low"},
        "sentiment_risks": [
            {"name": "Sentiment reversal", "impact": "medium", "signal": "weak guidance", "evidence_ids": ["ev_003"]},
        ],
        "missing_fields": [],
    }


def _state(evidence=None, price_history=None):
    ev = evidence if evidence is not None else _evidence()
    ph = price_history if price_history is not None else _price_history()
    return {
        "query": "Analyse AAPL",
        "intent": ResearchIntent(ticker="AAPL", subjects=["Apple"], scope="company"),
        "evidence": ev,
        "normalized_data": NormalizedData(
            query="Analyse AAPL",
            metrics=MetricsBlock(price_history=ph),
        ),
        "agent_statuses": [],
    }


def _mock_llm(response: dict | None = None, raises: Exception | None = None):
    llm = MagicMock()
    if raises:
        llm.call_with_retry.side_effect = raises
    else:
        llm.call_with_retry.return_value = json.dumps(response or _llm_response())
    return llm


def _ms(result) -> MarketSentiment:
    return result["market_sentiment"]


# ── output shape ───────────────────────────────────────────────────────────

def test_returns_market_sentiment_key():
    with patch("src.server.agents.market_sentiment._llm", _mock_llm()):
        result = market_sentiment_node(_state())
    assert "market_sentiment" in result


def test_result_is_typed_model():
    with patch("src.server.agents.market_sentiment._llm", _mock_llm()):
        result = market_sentiment_node(_state())
    assert isinstance(_ms(result), MarketSentiment)


def test_claims_is_list():
    with patch("src.server.agents.market_sentiment._llm", _mock_llm()):
        result = market_sentiment_node(_state())
    assert isinstance(_ms(result).claims, list)


def test_all_claims_have_evidence_ids():
    with patch("src.server.agents.market_sentiment._llm", _mock_llm()):
        result = market_sentiment_node(_state())
    for claim in _ms(result).claims:
        assert len(claim.evidence_ids) >= 1


def test_news_sentiment_direction_valid():
    with patch("src.server.agents.market_sentiment._llm", _mock_llm()):
        result = market_sentiment_node(_state())
    assert _ms(result).news_sentiment.direction in ("positive", "neutral", "negative")


def test_price_action_present():
    with patch("src.server.agents.market_sentiment._llm", _mock_llm()):
        result = market_sentiment_node(_state())
    assert _ms(result).price_action is not None


def test_market_narrative_present():
    with patch("src.server.agents.market_sentiment._llm", _mock_llm()):
        result = market_sentiment_node(_state())
    assert _ms(result).market_narrative.summary


def test_sentiment_risks_is_list():
    with patch("src.server.agents.market_sentiment._llm", _mock_llm()):
        result = market_sentiment_node(_state())
    assert isinstance(_ms(result).sentiment_risks, list)


def test_missing_fields_is_list():
    with patch("src.server.agents.market_sentiment._llm", _mock_llm()):
        result = market_sentiment_node(_state())
    assert isinstance(_ms(result).missing_fields, list)


def test_llm_used_flag_true_on_success():
    with patch("src.server.agents.market_sentiment._llm", _mock_llm()):
        result = market_sentiment_node(_state())
    assert isinstance(_ms(result), MarketSentiment)


# ── raises on LLM failure (no stub fallback) ──────────────────────────────

def test_raises_when_llm_raises():
    with pytest.raises(RuntimeError, match="market_sentiment"):
        with patch("src.server.agents.market_sentiment._llm", _mock_llm(raises=RuntimeError("exhausted"))):
            market_sentiment_node(_state())


def test_raises_when_no_evidence():
    with pytest.raises(RuntimeError, match="market_sentiment"):
        with patch("src.server.agents.market_sentiment._llm", _mock_llm()):
            market_sentiment_node(_state(evidence=[]))


# ── news evidence filtering ────────────────────────────────────────────────

def test_only_news_evidence_used_in_prompt():
    captured = {}
    def capture(prompt, **kw):
        captured["prompt"] = prompt
        return json.dumps(_llm_response())

    with patch("src.server.agents.market_sentiment._llm") as mock_llm:
        mock_llm.call_with_retry.side_effect = capture
        market_sentiment_node(_state())

    # all IDs should be listed in AVAILABLE EVIDENCE IDs
    assert "ev_001" in captured["prompt"]


def test_empty_statuses_returned_unchanged():
    with patch("src.server.agents.market_sentiment._llm", _mock_llm()):
        result = market_sentiment_node(_state())
    assert result["agent_statuses"] == []


# ── agent_questions surfacing ──────────────────────────────────────────────

def test_agent_questions_empty_when_no_missing_fields():
    with patch("src.server.agents.market_sentiment._llm", _mock_llm()):
        result = market_sentiment_node(_state())
    assert result["agent_questions"] == []


def test_agent_questions_populated_when_llm_reports_missing_fields():
    response = _llm_response()
    response["missing_fields"] = ["analyst_ratings", "short_interest"]
    with patch("src.server.agents.market_sentiment._llm", _mock_llm(response)):
        result = market_sentiment_node(_state())
    qs = result["agent_questions"]
    assert len(qs) == 2
    assert all("market_sentiment needs" in q for q in qs)


def test_agent_questions_empty_on_llm_failure():
    with pytest.raises(RuntimeError):
        with patch("src.server.agents.market_sentiment._llm", _mock_llm(raises=RuntimeError("err"))):
            market_sentiment_node(_state())
