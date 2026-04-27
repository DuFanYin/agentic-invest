"""Unit tests for research_node — all external calls mocked."""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock, patch

from src.server.agents.research import research_node, _detect_conflicts
from src.server.models.analysis import NormalizedData
from src.server.models.intent import ResearchIntent
from src.server.models.evidence import Evidence


def _run(coro):
    return asyncio.run(coro)


def _intent(ticker: str = "AAPL") -> ResearchIntent:
    return ResearchIntent(ticker=ticker, subjects=["Apple Inc"], scope="company")


def _base_state(ticker: str = "AAPL", pass_id: int = 0) -> dict:
    return {
        "query": f"Analyse {ticker}",
        "intent": _intent(ticker),
        "research_pass": pass_id,
        "open_questions": [],
        "agent_statuses": [],
    }


def _mock_finance(
    info: dict | None = None,
    financials: dict | None = None,
    price: dict | None = None,
    news: list | None = None,
):
    client = MagicMock()
    client.get_info.return_value = info or {
        "name": "Apple Inc",
        "sector": "Technology",
        "market_cap_fmt": "$3T",
        "pe_ratio": 28.5,
        "ev_ebitda": 22.1,
        "description": "Apple designs consumer electronics.",
    }
    client.get_financials.return_value = financials or {
        "ttm": {
            "revenue": 400_000_000_000,
            "gross_margin_pct": 44.5,
            "operating_margin_pct": 30.1,
            "net_income": 100_000_000_000,
        },
        "three_year_avg": {"revenue_growth_pct": 10.2, "operating_margin_pct": 28.0},
        "latest_quarter": {"revenue": 94_000_000_000, "eps": 1.52},
        "missing_fields": [],
    }
    client.get_price_history.return_value = price or {
        "return_1y_pct": 22.4,
        "return_30d_pct": 3.1,
        "annualised_volatility_pct": 18.7,
        "high_52w": 199.62,
        "low_52w": 124.17,
    }
    client.get_news.return_value = news or [
        {"title": "Apple reports record earnings", "url": "https://example.com/1", "published_at": "2026-01-01T00:00:00Z"},
        {"title": "Apple Vision Pro sales update", "url": "https://example.com/2", "published_at": "2026-01-02T00:00:00Z"},
    ]
    return client


_WEB_DEFAULTS = [
    {"title": "Web result 1", "url": "https://web.example.com/1", "content": "Analysis...", "published_date": None, "score": 0.8},
    {"title": "Web result 2", "url": "https://web.example.com/2", "content": "More analysis...", "published_date": None, "score": 0.7},
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


def _patch(finance=None, web=None):
    """Context manager helper — patches _finance, _web, and _cache."""
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(patch("src.server.agents.research._finance", finance or _mock_finance()))
    stack.enter_context(patch("src.server.agents.research._web", web or _mock_web()))
    stack.enter_context(patch("src.server.agents.research._cache", _mock_cache()))
    return stack


# ── Basic shape ────────────────────────────────────────────────────────────

def test_all_evidence_have_required_fields():
    with _patch():
        result = _run(research_node(_base_state()))
    for ev in result["evidence"]:
        assert ev.id
        assert ev.source_type
        assert ev.summary
        assert ev.retrieved_at


def test_evidence_ids_are_unique():
    with _patch():
        result = _run(research_node(_base_state()))
    ids = [ev.id for ev in result["evidence"]]
    assert len(ids) == len(set(ids))


def test_normalized_data_has_metrics():
    with _patch():
        result = _run(research_node(_base_state()))
    nd = result["normalized_data"]
    assert isinstance(nd, NormalizedData)
    assert nd.metrics.ttm
    assert nd.metrics.three_year_avg is not None
    assert nd.metrics.latest_quarter is not None


def test_research_pass_incremented():
    with _patch():
        result = _run(research_node(_base_state(pass_id=0)))
    assert result["research_pass"] == 1


# ── Source types ───────────────────────────────────────────────────────────

def test_news_capped_at_five():
    many_news = [
        {"title": f"Headline {i}", "url": f"https://example.com/{i}", "published_at": None}
        for i in range(10)
    ]
    with _patch(finance=_mock_finance(news=many_news)):
        result = _run(research_node(_base_state()))
    news_items = [ev for ev in result["evidence"] if ev.source_type == "news"]
    assert len(news_items) <= 5


# ── Web result cleaning ────────────────────────────────────────────────────

def test_web_results_are_cleaned_for_duplicate_and_empty_urls():
    web = _mock_web([
        {"title": "Duplicate", "url": "https://example.com/1", "content": "...", "published_date": None, "score": 0.9},
        {"title": "Unique", "url": "https://web.example.com/unique", "content": "...", "published_date": None, "score": 0.8},
        {"title": "No URL", "url": "", "content": "...", "published_date": None, "score": 0.5},
    ])
    with _patch(web=web):
        result = _run(research_node(_base_state()))
    urls = [ev.url for ev in result["evidence"]]
    assert urls.count("https://example.com/1") == 1
    web_items = [ev for ev in result["evidence"] if ev.source_type == "web"]
    assert all(ev.url for ev in web_items)


def test_web_search_called_with_query():
    web = _mock_web()
    with _patch(web=web):
        _run(research_node(_base_state()))
    web.search.assert_called_once()


# ── Missing fields propagated ──────────────────────────────────────────────

def test_missing_fields_from_financials_propagated():
    fin = {
        "ttm": {"revenue": None, "gross_margin_pct": None, "operating_margin_pct": None, "net_income": None},
        "three_year_avg": {},
        "latest_quarter": {},
        "missing_fields": ["revenue", "gross_margin_pct"],
    }
    with _patch(finance=_mock_finance(financials=fin)):
        result = _run(research_node(_base_state()))
    assert "revenue" in result["normalized_data"].missing_fields


# ── Fallback when no ticker ────────────────────────────────────────────────

def test_no_ticker_web_search_still_runs():
    web = _mock_web()
    state = {
        "query": "What are good ETFs?",
        "intent": ResearchIntent(ticker=None, subjects=[], scope="general"),
        "research_pass": 0,
        "open_questions": [],
        "agent_statuses": [],
    }
    with _patch(web=web):
        result = _run(research_node(state))
    web.search.assert_called_once()
    assert len(result["evidence"]) >= 1
    assert all(ev.source_type == "web" for ev in result["evidence"])


def test_no_ticker_no_web_results_raises():
    web = _mock_web([])
    state = {
        "query": "What are good ETFs?",
        "intent": ResearchIntent(ticker=None, subjects=[], scope="general"),
        "research_pass": 0,
        "open_questions": [],
        "agent_statuses": [],
    }
    with pytest.raises(RuntimeError, match="research"):
        with _patch(finance=_mock_finance(), web=web):
            _run(research_node(state))


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
    with _patch(web=web):
        result = _run(research_node(_base_state()))
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
        with _patch(finance=finance, web=web):
            _run(research_node(_base_state()))


# ── Id offset on multi-pass ────────────────────────────────────────────────

def test_evidence_ids_offset_on_second_pass():
    with _patch():
        result = _run(research_node(_base_state(pass_id=1)))
    for ev in result["evidence"]:
        num = int(ev.id.split("_")[1])
        assert num >= 100


# ── Price history in metrics ───────────────────────────────────────────────

def test_price_history_stored_in_metrics():
    with _patch():
        result = _run(research_node(_base_state()))
    assert result["normalized_data"].metrics.price_history
    assert result["normalized_data"].metrics.price_history["return_1y_pct"] == 22.4


# ── Conflict detection ─────────────────────────────────────────────────────

def test_conflict_detected_on_reliability_divergence():
    ev = [
        Evidence(id="ev_001", source_type="financial_api", title="A", summary="s",
                 reliability="high", retrieved_at="2026-01-01T00:00:00Z",
                 related_topics=["valuation"]),
        Evidence(id="ev_002", source_type="web", title="B", summary="s",
                 reliability="low", retrieved_at="2026-01-01T00:00:00Z",
                 related_topics=["valuation"]),
    ]
    conflicts = _detect_conflicts(ev, {})
    assert len(conflicts) == 1
    assert conflicts[0]["topic"] == "valuation"
    assert conflicts[0]["type"] == "reliability_divergence"


def test_conflicts_stored_in_normalized_data():
    finance = _mock_finance()
    finance.get_info.return_value = {
        "name": "Apple Inc", "sector": "Technology",
        "market_cap_fmt": "$3T", "pe_ratio": 28.5, "ev_ebitda": 22.1, "description": "",
    }
    web = _mock_web([{
        "title": "Bearish valuation view",
        "url": "https://bearish.com/1",
        "content": "valuation looks stretched",
        "published_date": None,
        "score": 0.6,
    }])
    with _patch(finance=finance, web=web):
        result = _run(research_node(_base_state()))
    nd = result["normalized_data"]
    assert isinstance(nd, NormalizedData)
    assert isinstance(nd.conflicts, list)
