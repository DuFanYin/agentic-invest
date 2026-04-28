"""Unit tests for market_sentiment_node — LLM injected directly."""

from __future__ import annotations

import asyncio
import pytest
import json
from unittest.mock import AsyncMock, MagicMock

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
        llm.call_with_retry = AsyncMock(side_effect=raises)
    else:
        llm.call_with_retry = AsyncMock(return_value=json.dumps(response or _llm_response()))
    return llm


def _run(coro):
    return asyncio.run(coro)


def _ms(result) -> MarketSentiment:
    return result["market_sentiment"]


# ── output shape ───────────────────────────────────────────────────────────

def test_result_shape_and_core_fields():
    result = _run(market_sentiment_node(_state(), llm=_mock_llm()))
    ms = _ms(result)
    assert isinstance(ms, MarketSentiment)


# ── best-effort degraded on LLM failure ───────────────────────────────────

def test_degraded_when_llm_raises():
    result = _run(market_sentiment_node(_state(), llm=_mock_llm(raises=RuntimeError("exhausted"))))
    ms = result["market_sentiment"]
    assert isinstance(ms, MarketSentiment)
    assert ms.degraded is True
    assert ms.news_sentiment.direction == "neutral"


def test_degraded_when_no_evidence():
    result = _run(market_sentiment_node(_state(evidence=[]), llm=_mock_llm()))
    ms = result["market_sentiment"]
    assert isinstance(ms, MarketSentiment)
    assert ms.degraded is True


# ── news evidence filtering ────────────────────────────────────────────────

