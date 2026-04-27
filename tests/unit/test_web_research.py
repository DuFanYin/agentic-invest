"""Unit tests for WebResearchClient — all HTTP calls mocked."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx as _httpx
import pytest

from src.server.services.web_research import WebResearchClient

_DEFAULT_RESULTS = [
    {
        "title": "Apple beats earnings estimates",
        "url": "https://example.com/apple-earnings",
        "content": "Apple reported record revenue...",
        "published_date": "2026-01-15",
        "score": 0.91,
    },
    {
        "title": "iPhone sales surge in Asia",
        "url": "https://example.com/iphone-asia",
        "content": "Demand for iPhone 16 remains strong...",
        "published_date": "2026-01-10",
        "score": 0.84,
    },
]


def _ok_response(results: list[dict] | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"results": _DEFAULT_RESULTS if results is None else results}
    return resp


def _error_response(status: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = json.dumps({"error": "bad request"})
    return resp


def _client(api_key: str = "tvly-test") -> WebResearchClient:
    return WebResearchClient(api_key=api_key)


# ── result shape ───────────────────────────────────────────────────────────

def test_result_has_required_fields():
    with patch("httpx.Client") as mock_http:
        mock_http.return_value.__enter__.return_value.post.return_value = _ok_response()
        results = _client().search("Apple earnings")
    assert len(results) == 2
    for r in results:
        assert "title" in r
        assert "url" in r
        assert "content" in r
        assert "retrieved_at" in r


# ── missing API key ────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,args", [
    ("search", ("Apple",)),
    ("search_news", ("AAPL",)),
])
def test_methods_return_empty_when_no_api_key(method, args):
    with patch("src.server.services.web_research.TAVILY_API_KEY", None):
        client = WebResearchClient()
    assert getattr(client, method)(*args) == []


# ── HTTP error responses ───────────────────────────────────────────────────

@pytest.mark.parametrize("status", [401, 429, 500])
def test_search_returns_empty_on_http_errors(status):
    with patch("httpx.Client") as mock_http:
        mock_http.return_value.__enter__.return_value.post.return_value = _error_response(status)
        assert _client().search("Apple") == []


# ── network failures ───────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "exc",
    [_httpx.TimeoutException("timed out"), _httpx.NetworkError("unreachable")],
)
def test_search_returns_empty_on_network_failures(exc):
    with patch("httpx.Client") as mock_http:
        mock_http.return_value.__enter__.return_value.post.side_effect = exc
        assert _client().search("Apple") == []


# ── URL filtering ──────────────────────────────────────────────────────────

def test_results_without_url_are_dropped():
    raw = [
        {"title": "Good result", "url": "https://example.com/good", "content": "...", "score": 0.9},
        {"title": "No URL result", "url": "", "content": "...", "score": 0.5},
        {"title": "Missing URL key", "content": "...", "score": 0.3},
    ]
    with patch("httpx.Client") as mock_http:
        mock_http.return_value.__enter__.return_value.post.return_value = _ok_response(raw)
        results = _client().search("test")
    assert len(results) == 1
    assert results[0]["title"] == "Good result"


def test_search_returns_empty_list_on_no_results():
    with patch("httpx.Client") as mock_http:
        mock_http.return_value.__enter__.return_value.post.return_value = _ok_response([])
        assert _client().search("obscure query xyz") == []


# ── search_news ────────────────────────────────────────────────────────────

def test_search_news_calls_search_with_ticker_in_query():
    captured = {}
    def capture(*a, **kw):
        captured["payload"] = kw.get("json", {})
        return _ok_response()

    with patch("httpx.Client") as mock_http:
        mock_http.return_value.__enter__.return_value.post.side_effect = capture
        _client().search_news("AAPL", days=30)

    assert "AAPL" in captured["payload"]["query"]
