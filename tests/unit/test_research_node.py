"""Unit tests for research_node — all external calls mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.server.agents.research import research_node
from src.server.models.intent import ResearchIntent
from src.server.models.evidence import Evidence


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


# ── Basic shape ────────────────────────────────────────────────────────────

def test_returns_evidence_list():
    with patch("src.server.agents.research._finance", _mock_finance()):
        result = research_node(_base_state())
    assert isinstance(result["evidence"], list)
    assert len(result["evidence"]) >= 1


def test_all_evidence_have_required_fields():
    with patch("src.server.agents.research._finance", _mock_finance()):
        result = research_node(_base_state())
    for ev in result["evidence"]:
        assert ev.id
        assert ev.source_type
        assert ev.summary
        assert ev.retrieved_at


def test_evidence_ids_are_unique():
    with patch("src.server.agents.research._finance", _mock_finance()):
        result = research_node(_base_state())
    ids = [ev.id for ev in result["evidence"]]
    assert len(ids) == len(set(ids))


def test_normalized_data_has_metrics():
    with patch("src.server.agents.research._finance", _mock_finance()):
        result = research_node(_base_state())
    nd = result["normalized_data"]
    assert "metrics" in nd
    assert "ttm" in nd["metrics"]
    assert "three_year_avg" in nd["metrics"]
    assert "latest_quarter" in nd["metrics"]


def test_normalized_data_has_missing_fields_list():
    with patch("src.server.agents.research._finance", _mock_finance()):
        result = research_node(_base_state())
    assert isinstance(result["normalized_data"]["missing_fields"], list)


def test_research_pass_incremented():
    with patch("src.server.agents.research._finance", _mock_finance()):
        result = research_node(_base_state(pass_id=0))
    assert result["research_pass"] == 1


def test_research_pass_incremented_on_second_pass():
    with patch("src.server.agents.research._finance", _mock_finance()):
        result = research_node(_base_state(pass_id=1))
    assert result["research_pass"] == 2


# ── Source types ───────────────────────────────────────────────────────────

def test_financial_api_evidence_present():
    with patch("src.server.agents.research._finance", _mock_finance()):
        result = research_node(_base_state())
    types = [ev.source_type for ev in result["evidence"]]
    assert "financial_api" in types


def test_news_evidence_present():
    with patch("src.server.agents.research._finance", _mock_finance()):
        result = research_node(_base_state())
    types = [ev.source_type for ev in result["evidence"]]
    assert "news" in types


def test_news_capped_at_five():
    many_news = [
        {"title": f"Headline {i}", "url": f"https://example.com/{i}", "published_at": None}
        for i in range(10)
    ]
    with patch("src.server.agents.research._finance", _mock_finance(news=many_news)):
        result = research_node(_base_state())
    news_items = [ev for ev in result["evidence"] if ev.source_type == "news"]
    assert len(news_items) <= 5


# ── Missing fields propagated ──────────────────────────────────────────────

def test_missing_fields_from_financials_propagated():
    fin = {
        "ttm": {"revenue": None, "gross_margin_pct": None, "operating_margin_pct": None, "net_income": None},
        "three_year_avg": {},
        "latest_quarter": {},
        "missing_fields": ["revenue", "gross_margin_pct"],
    }
    with patch("src.server.agents.research._finance", _mock_finance(financials=fin)):
        result = research_node(_base_state())
    assert "revenue" in result["normalized_data"]["missing_fields"]


# ── Fallback when no ticker ────────────────────────────────────────────────

def test_no_ticker_produces_fallback_evidence():
    state = {
        "query": "What are good ETFs?",
        "intent": ResearchIntent(ticker=None, subjects=[], scope="general"),
        "research_pass": 0,
        "open_questions": [],
        "agent_statuses": [],
    }
    with patch("src.server.agents.research._finance", _mock_finance()):
        result = research_node(state)
    assert len(result["evidence"]) >= 1
    assert result["evidence"][0].source_type == "web"


# ── Resilience: individual service failures ────────────────────────────────

def test_get_info_failure_does_not_crash():
    client = _mock_finance()
    client.get_info.side_effect = Exception("network error")
    with patch("src.server.agents.research._finance", client):
        result = research_node(_base_state())
    assert isinstance(result["evidence"], list)


def test_get_financials_failure_does_not_crash():
    client = _mock_finance()
    client.get_financials.side_effect = Exception("timeout")
    with patch("src.server.agents.research._finance", client):
        result = research_node(_base_state())
    assert isinstance(result["evidence"], list)


def test_get_price_history_failure_does_not_crash():
    client = _mock_finance()
    client.get_price_history.side_effect = Exception("rate limit")
    with patch("src.server.agents.research._finance", client):
        result = research_node(_base_state())
    assert isinstance(result["evidence"], list)


def test_all_services_fail_produces_fallback():
    client = _mock_finance()
    client.get_info.side_effect = Exception("err")
    client.get_financials.side_effect = Exception("err")
    client.get_price_history.side_effect = Exception("err")
    client.get_news.side_effect = Exception("err")
    with patch("src.server.agents.research._finance", client):
        result = research_node(_base_state())
    assert len(result["evidence"]) >= 1


# ── Id offset on multi-pass ────────────────────────────────────────────────

def test_evidence_ids_offset_on_second_pass():
    with patch("src.server.agents.research._finance", _mock_finance()):
        result = research_node(_base_state(pass_id=1))
    for ev in result["evidence"]:
        num = int(ev.id.split("_")[1])
        assert num >= 100  # offset = pass_id(1) * 100


# ── Price history in metrics ───────────────────────────────────────────────

def test_price_history_stored_in_metrics():
    with patch("src.server.agents.research._finance", _mock_finance()):
        result = research_node(_base_state())
    assert "price_history" in result["normalized_data"]["metrics"]
    assert result["normalized_data"]["metrics"]["price_history"]["return_1y_pct"] == 22.4
