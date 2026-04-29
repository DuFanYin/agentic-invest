"""
Tavily web/news search client.

Gracefully degrades to [] when TAVILY_API_KEY is absent — the rest of the
pipeline treats missing web evidence as non-fatal.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from src.server.config import TAVILY_API_KEY
from src.server.services.retry import (
    DEFAULT_FETCH_TIMEOUT_SECONDS,
    RETRYABLE_HTTP_STATUS,
    RetryableFetchError,
    retry_sync,
)

logger = logging.getLogger(__name__)

_TAVILY_URL = "https://api.tavily.com/search"
_DEFAULT_MAX_RESULTS = 5


def _normalise(raw: dict, retrieved_at: str) -> dict:
    return {
        "title": raw.get("title", ""),
        "url": raw.get("url", ""),
        "content": raw.get("content", "") or raw.get("snippet", ""),
        "published_date": raw.get("published_date"),
        "score": raw.get("score"),
        "retrieved_at": retrieved_at,
    }


class WebResearchClient:
    def __init__(self, *, api_key: str | None = None, timeout_seconds: float = DEFAULT_FETCH_TIMEOUT_SECONDS) -> None:
        self.api_key = api_key or TAVILY_API_KEY
        self.timeout = timeout_seconds

    # ── public API ─────────────────────────────────────────────────────────

    def search(self, query: str, max_results: int = _DEFAULT_MAX_RESULTS) -> list[dict]:
        """
        General web search. Returns [] if the API key is absent or the call fails.
        Each result: { title, url, content, published_date, score, retrieved_at }
        """
        if not self.api_key:
            logger.warning("TAVILY_API_KEY not set — skipping web search")
            return []

        retrieved_at = datetime.now(UTC).isoformat()
        payload = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
        }

        def _request() -> httpx.Response:
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(_TAVILY_URL, json=payload)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise RetryableFetchError(str(exc)) from exc
            if response.status_code in RETRYABLE_HTTP_STATUS:
                raise RetryableFetchError(f"http {response.status_code}")
            return response

        try:
            response = retry_sync(_request, op_name="tavily.search")
        except Exception as exc:
            logger.warning("Tavily request failed: %s", exc)
            return []

        if response.status_code != 200:
            logger.warning("Tavily returned HTTP %d: %s", response.status_code, response.text[:200])
            return []

        try:
            data = response.json()
            results = data.get("results", [])
        except Exception as exc:
            logger.warning("Tavily response parse error: %s", exc)
            return []

        return [_normalise(r, retrieved_at) for r in results if r.get("url")]

    def search_news(self, ticker: str, days: int = 30, max_results: int = _DEFAULT_MAX_RESULTS) -> list[dict]:
        """
        News-focused search for a ticker. Filters to recent articles via query framing.
        Returns [] on missing key or any failure.
        """
        query = f"{ticker} stock news last {days} days"
        return self.search(query, max_results=max_results)
