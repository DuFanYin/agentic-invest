"""Unit tests for WebResearchClient — all HTTP calls mocked."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

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
    # Use explicit sentinel so passing [] actually gives [] (not the default)
    resp.json.return_value = {"results": _DEFAULT_RESULTS if results is None else results}
    return resp


def _error_response(status: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = json.dumps({"error": "bad request"})
    return resp


def _client(api_key: str = "tvly-test") -> WebResearchClient:
    with patch("src.server.services.web_research._load_env"):
        return WebResearchClient(api_key=api_key)


# ── result shape ───────────────────────────────────────────────────────────

def test_search_returns_list():
    with patch("httpx.Client") as mock_http:
        mock_http.return_value.__enter__.return_value.post.return_value = _ok_response()
        result = _client().search("Apple earnings")
    assert isinstance(result, list)
    assert len(result) == 2


def test_result_has_required_fields():
    with patch("httpx.Client") as mock_http:
        mock_http.return_value.__enter__.return_value.post.return_value = _ok_response()
        results = _client().search("Apple earnings")
    for r in results:
        assert "title" in r
        assert "url" in r
        assert "content" in r
        assert "retrieved_at" in r


def test_result_url_is_string():
    with patch("httpx.Client") as mock_http:
        mock_http.return_value.__enter__.return_value.post.return_value = _ok_response()
        results = _client().search("Apple")
    assert all(isinstance(r["url"], str) for r in results)


def test_published_date_and_score_passed_through():
    with patch("httpx.Client") as mock_http:
        mock_http.return_value.__enter__.return_value.post.return_value = _ok_response()
        results = _client().search("Apple")
    assert results[0]["published_date"] == "2026-01-15"
    assert results[0]["score"] == 0.91


# ── missing API key ────────────────────────────────────────────────────────

def test_search_returns_empty_when_no_api_key():
    with patch("src.server.services.web_research._load_env"):
        with patch.dict("os.environ", {}, clear=True):
            client = WebResearchClient()
    result = client.search("Apple")
    assert result == []


# ── HTTP error responses ───────────────────────────────────────────────────

def test_search_returns_empty_on_401():
    with patch("httpx.Client") as mock_http:
        mock_http.return_value.__enter__.return_value.post.return_value = _error_response(401)
        result = _client().search("Apple")
    assert result == []


def test_search_returns_empty_on_429():
    with patch("httpx.Client") as mock_http:
        mock_http.return_value.__enter__.return_value.post.return_value = _error_response(429)
        result = _client().search("Apple")
    assert result == []


def test_search_returns_empty_on_500():
    with patch("httpx.Client") as mock_http:
        mock_http.return_value.__enter__.return_value.post.return_value = _error_response(500)
        result = _client().search("Apple")
    assert result == []


# ── network failures ───────────────────────────────────────────────────────

def test_search_returns_empty_on_timeout():
    import httpx as _httpx
    with patch("httpx.Client") as mock_http:
        mock_http.return_value.__enter__.return_value.post.side_effect = _httpx.TimeoutException("timed out")
        result = _client().search("Apple")
    assert result == []


def test_search_returns_empty_on_network_error():
    import httpx as _httpx
    with patch("httpx.Client") as mock_http:
        mock_http.return_value.__enter__.return_value.post.side_effect = _httpx.NetworkError("unreachable")
        result = _client().search("Apple")
    assert result == []


# ── results without URL are filtered out ──────────────────────────────────

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


# ── empty results from API ─────────────────────────────────────────────────

def test_search_returns_empty_list_on_no_results():
    with patch("httpx.Client") as mock_http:
        mock_http.return_value.__enter__.return_value.post.return_value = _ok_response([])
        result = _client().search("obscure query xyz")
    assert result == []


# ── search_news ────────────────────────────────────────────────────────────

def test_search_news_calls_search_with_ticker_query():
    captured = {}
    def capture(*a, **kw):
        captured["payload"] = kw.get("json", {})
        return _ok_response()

    with patch("httpx.Client") as mock_http:
        mock_http.return_value.__enter__.return_value.post.side_effect = capture
        _client().search_news("AAPL", days=30)

    assert "AAPL" in captured["payload"]["query"]


def test_search_news_returns_empty_when_no_api_key():
    with patch("src.server.services.web_research._load_env"):
        with patch.dict("os.environ", {}, clear=True):
            client = WebResearchClient()
    result = client.search_news("AAPL")
    assert result == []


def test_search_news_result_shape():
    with patch("httpx.Client") as mock_http:
        mock_http.return_value.__enter__.return_value.post.return_value = _ok_response()
        results = _client().search_news("AAPL")
    assert isinstance(results, list)
    if results:
        assert "url" in results[0]
        assert "content" in results[0]
