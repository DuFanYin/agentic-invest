"""Unit tests for the three parallel analysis nodes — fundamental, macro, market sentiment."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from src.server.agents.fundamental_analysis import fundamental_analysis_node
from src.server.agents.macro_analysis import macro_analysis_node
from src.server.agents.market_sentiment import market_sentiment_node
from src.server.models.analysis import FundamentalAnalysis, MacroAnalysis, MarketSentiment, MetricsBlock, NormalizedData
from src.server.models.evidence import Evidence
from src.server.models.intent import ResearchIntent


def _run(coro):
    return asyncio.run(coro)


def _evidence(n: int = 3, source_type: str = "financial_api") -> list[Evidence]:
    return [
        Evidence(
            id=f"ev_{i:03d}",
            source_type=source_type,
            title=f"Evidence {i}",
            summary=f"Summary {i}",
            reliability="high",
            retrieved_at="2026-01-01T00:00:00Z",
        )
        for i in range(1, n + 1)
    ]


def _mock_llm(response: dict, raises: Exception | None = None):
    llm = MagicMock()
    if raises:
        llm.call_with_retry = AsyncMock(side_effect=raises)
    else:
        llm.call_with_retry = AsyncMock(return_value=json.dumps(response))
    return llm


_INTENT = ResearchIntent(ticker="AAPL", subjects=["Apple"], scope="company")


# ── fundamental_analysis_node ─────────────────────────────────────────────


_FA_RESPONSE = {
    "claims": [{"statement": "Strong margins.", "confidence": "high", "evidence_ids": ["ev_001"]}],
    "business_quality": {"view": "stable", "drivers": ["brand"]},
    "financials": {"profitability_trend": "improving", "cash_flow_quality": "high"},
    "valuation": {"relative_multiple_view": "near median", "simplified_dcf_view": "fair"},
    "fundamental_risks": [],
    "missing_fields": [],
}


def _fa_state(evidence=None):
    return {
        "query": "Analyse AAPL",
        "intent": _INTENT,
        "evidence": evidence if evidence is not None else _evidence(),
        "normalized_data": NormalizedData(
            query="Analyse AAPL", metrics=MetricsBlock(ttm={"revenue": 400e9}, three_year_avg={}, latest_quarter={})
        ),
        "agent_statuses": [],
    }


def test_fundamental_happy_path():
    result = _run(fundamental_analysis_node(_fa_state(), llm=_mock_llm(_FA_RESPONSE)))
    fa = result["fundamental_analysis"]
    assert isinstance(fa, FundamentalAnalysis)
    assert not fa.degraded
    assert len(fa.claims) >= 1


@pytest.mark.parametrize("evidence,raises", [([], None), (None, RuntimeError("exhausted"))])
def test_fundamental_degrades_on_failure(evidence, raises):
    result = _run(fundamental_analysis_node(_fa_state(evidence=evidence), llm=_mock_llm(_FA_RESPONSE, raises=raises)))
    fa = result["fundamental_analysis"]
    assert isinstance(fa, FundamentalAnalysis)
    assert fa.degraded is True


# ── macro_analysis_node ───────────────────────────────────────────────────


_MACRO_RESPONSE = {
    "macro_view": "Easing cycle underway.",
    "macro_drivers": ["Fed cuts"],
    "macro_risks": [],
    "macro_signals": [],
    "rate_environment": "easing",
    "growth_environment": "expanding",
    "missing_fields": [],
}


def _macro_state(evidence=None):
    return {
        "query": "Analyse AAPL",
        "intent": _INTENT,
        "evidence": evidence if evidence is not None else _evidence(source_type="macro_api"),
        "agent_statuses": [],
    }


def test_macro_degrades_on_llm_failure():
    result = _run(macro_analysis_node(_macro_state(), llm=_mock_llm(_MACRO_RESPONSE, raises=RuntimeError("exhausted"))))
    macro = result["macro_analysis"]
    assert isinstance(macro, MacroAnalysis)
    assert macro.degraded is True


# ── market_sentiment_node ─────────────────────────────────────────────────


_MS_RESPONSE = {
    "claims": [{"statement": "Positive.", "confidence": "medium", "evidence_ids": ["ev_001"]}],
    "news_sentiment": {"direction": "positive", "confidence": "medium"},
    "price_action": {"trend": "upward", "return_30d_pct": 3.1, "volatility": "medium"},
    "market_narrative": {"summary": "Optimistic.", "crowding_risk": "low"},
    "sentiment_risks": [],
    "missing_fields": [],
}


def _ms_state(evidence=None):
    return {
        "query": "Analyse AAPL",
        "intent": _INTENT,
        "evidence": evidence if evidence is not None else _evidence(source_type="news"),
        "normalized_data": NormalizedData(query="Analyse AAPL", metrics=MetricsBlock()),
        "agent_statuses": [],
    }


@pytest.mark.parametrize("evidence,raises", [([], None), (None, RuntimeError("exhausted"))])
def test_sentiment_degrades_on_failure(evidence, raises):
    result = _run(market_sentiment_node(_ms_state(evidence=evidence), llm=_mock_llm(_MS_RESPONSE, raises=raises)))
    ms = result["market_sentiment"]
    assert isinstance(ms, MarketSentiment)
    assert ms.degraded is True
