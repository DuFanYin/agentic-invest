"""Unit tests for research_node — all external calls mocked via constructor injection."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from src.server.agents.research import research_node
from src.server.capabilities.normalize import detect_conflicts
from src.server.models.analysis import NormalizedData
from src.server.models.evidence import Evidence
from src.server.models.intent import ResearchIntent


def _run(coro):
    return asyncio.run(coro)


def _intent(ticker: str = "AAPL") -> ResearchIntent:
    return ResearchIntent(ticker=ticker, subjects=["Apple Inc"], scope="company")


def _base_state(ticker: str = "AAPL", iteration_id: int = 0) -> dict:
    return {
        "query": f"Analyse {ticker}",
        "intent": _intent(ticker),
        "research_iteration": iteration_id,
        "retry_questions": [],
        "agent_statuses": [],
    }


def _mock_finance(
    info: dict | None = None, financials: dict | None = None, price: dict | None = None, news: list | None = None
):
    client = MagicMock()
    client.get_info.return_value = info or {
        "name": "Apple Inc",
        "sector": "Technology",
        "market_cap": 3_000_000_000_000,
        "trailing_pe": 28.5,
        "ev_to_ebitda": 22.1,
        "description": "Apple designs consumer electronics.",
    }
    client.get_financials.return_value = financials or {
        "ttm": {
            "revenue": 400_000_000_000,
            "gross_margin_pct": 44.5,
            "operating_margin_pct": 30.1,
            "net_margin_pct": 25.0,
        },
        "three_year_avg": {"revenue_growth_pct": 10.2, "operating_margin_pct": 28.0},
        "latest_quarter": {"revenue": 94_000_000_000, "eps": 1.52},
        "missing_fields": [],
    }
    client.get_price_history.return_value = price or {
        "period_return_pct": 22.4,
        "return_30d_pct": 3.1,
        "volatility_annualised_pct": 18.7,
        "52w_high": 199.62,
        "52w_low": 124.17,
    }
    client.get_news.return_value = news or [
        {
            "title": "Apple reports record earnings",
            "url": "https://example.com/1",
            "published_at": "2026-01-01T00:00:00Z",
        },
        {
            "title": "Apple Vision Pro sales update",
            "url": "https://example.com/2",
            "published_at": "2026-01-02T00:00:00Z",
        },
    ]
    return client


_WEB_DEFAULTS = [
    {
        "title": "Web result 1",
        "url": "https://web.example.com/1",
        "content": "Analysis...",
        "published_date": None,
        "score": 0.8,
    },
    {
        "title": "Web result 2",
        "url": "https://web.example.com/2",
        "content": "More analysis...",
        "published_date": None,
        "score": 0.7,
    },
]


def _mock_web(results: list | None = None):
    client = MagicMock()
    client.search.return_value = _WEB_DEFAULTS if results is None else results
    return client


def _mock_cache():
    """Cache that always misses so service calls always go through."""
    c = MagicMock()
    c.get.return_value = None
    return c


def _mock_macro(fred: dict | None = None, signals: dict | None = None):
    """Macro client that returns empty data by default (no FRED key in tests)."""
    client = MagicMock()
    client.get_all = AsyncMock(
        return_value={
            "fred": fred if fred is not None else {},
            "market_signals": signals if signals is not None else {},
        }
    )
    return client


class _OkLLM:
    def __init__(self, queries: list[str]):
        self._queries = queries

    async def call_with_retry(self, prompt, *, system, node):
        return json.dumps({"queries": self._queries})


class _FailLLM:
    async def call_with_retry(self, prompt, *, system, node):
        raise RuntimeError("llm unavailable")


def _research_deps(**overrides):
    """Defaults match previous _patch behaviour: all mocked, LLM fails unless overridden."""
    base = {
        "llm": _FailLLM(),
        "cache": _mock_cache(),
        "finance_client": _mock_finance(),
        "macro_client": _mock_macro(),
        "web_client": _mock_web(),
    }
    base.update(overrides)
    return base


# ── Basic shape ────────────────────────────────────────────────────────────


def test_research_smoke_contract_and_metrics():
    result = _run(research_node(_base_state(), **_research_deps()))
    ids = [ev.id for ev in result["evidence"]]
    assert result["research_iteration"] == 1
    for ev in result["evidence"]:
        assert ev.id
        assert ev.source_type
        assert ev.summary
        assert ev.retrieved_at
    assert len(ids) == len(set(ids))
    nd = result["normalized_data"]
    assert isinstance(nd, NormalizedData)
    assert nd.metrics.ttm
    assert nd.metrics.three_year_avg is not None
    assert nd.metrics.latest_quarter is not None


# ── Fallback when no ticker ────────────────────────────────────────────────


def test_no_ticker_no_web_results_raises():
    web = _mock_web([])
    state = {
        "query": "What are good ETFs?",
        "intent": ResearchIntent(ticker=None, subjects=[], scope="general"),
        "research_iteration": 0,
        "retry_questions": [],
        "agent_statuses": [],
    }
    with pytest.raises(RuntimeError, match="research"):
        _run(research_node(state, **_research_deps(finance_client=_mock_finance(), web_client=web)))


# ── Resilience: individual service failures ────────────────────────────────


@pytest.mark.parametrize("failed_target", ["get_info", "get_financials", "web_search"])
def test_single_service_failure_does_not_crash(failed_target: str):
    finance = _mock_finance()
    web = _mock_web()
    if failed_target == "get_info":
        finance.get_info.side_effect = Exception("network error")
    elif failed_target == "get_financials":
        finance.get_financials.side_effect = Exception("timeout")
    else:
        web.search.side_effect = Exception("Tavily down")
    result = _run(research_node(_base_state(), **_research_deps(finance_client=finance, web_client=web)))
    assert isinstance(result["evidence"], list)


def test_all_services_fail_raises():
    finance = _mock_finance()
    finance.get_info.side_effect = Exception("err")
    finance.get_financials.side_effect = Exception("err")
    finance.get_price_history.side_effect = Exception("err")
    finance.get_news.side_effect = Exception("err")
    web = _mock_web()
    web.search.side_effect = Exception("err")
    with pytest.raises(RuntimeError, match="research"):
        _run(research_node(_base_state(), **_research_deps(finance_client=finance, web_client=web)))


def test_research_uses_llm_planned_multi_queries():
    web = _mock_web()
    llm_queries = ["Apple margin trend latest", "Apple segment revenue mix latest", "Apple guidance revision 2026"]
    llm = _OkLLM(llm_queries)

    result = _run(research_node(_base_state(), **_research_deps(llm=llm, web_client=web)))

    assert isinstance(result["evidence"], list)
    called_queries = [call.args[0] for call in web.search.call_args_list]
    assert web.search.call_count == 3
    assert set(called_queries) == set(llm_queries)


def test_research_falls_back_to_default_queries_when_llm_fails():
    web = _mock_web()
    state = _base_state()
    state["retry_questions"] = ["profitability trend"]

    result = _run(research_node(state, **_research_deps(llm=_FailLLM(), web_client=web)))

    assert isinstance(result["evidence"], list)
    called_queries = [call.args[0] for call in web.search.call_args_list]
    assert "Apple Inc profitability trend" in called_queries
    assert "Apple Inc investment analysis latest" in called_queries


# ── Conflict detection ─────────────────────────────────────────────────────


def test_conflict_detected_on_reliability_divergence():
    ev = [
        Evidence(
            id="ev_001",
            source_type="financial_api",
            title="A",
            summary="s",
            reliability="high",
            retrieved_at="2026-01-01T00:00:00Z",
            related_topics=["valuation"],
        ),
        Evidence(
            id="ev_002",
            source_type="web",
            title="B",
            summary="s",
            reliability="low",
            retrieved_at="2026-01-01T00:00:00Z",
            related_topics=["valuation"],
        ),
    ]
    conflicts = detect_conflicts(ev)
    assert len(conflicts) == 1
    assert conflicts[0]["topic"] == "valuation"
    assert conflicts[0]["type"] == "reliability_divergence"
