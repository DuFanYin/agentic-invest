"""
Central config — expose typed constants from environment variables.
.env loading is handled by main.py at server startup.
"""

from __future__ import annotations

import os
from pathlib import Path

repo_root = Path(__file__).resolve().parents[2]

# Required
LLM_PROVIDER: str = (os.getenv("LLM_PROVIDER") or "openrouter").strip().lower()
LLM_API_KEY: str | None = os.getenv("LLM_API_KEY") or None

# Derived runtime defaults (not environment-facing).
# All providers expose an OpenAI-compatible /chat/completions endpoint.
_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}
LLM_BASE_URL: str = _BASE_URLS.get(LLM_PROVIDER, _BASE_URLS["openrouter"])
# OpenRouter optional headers — set these in code if needed, not via env.
LLM_HTTP_REFERER: str | None = None
LLM_APP_TITLE: str | None = None

TAVILY_API_KEY: str | None = os.getenv("TAVILY_API_KEY") or None
FRED_API_KEY: str | None = os.getenv("FRED_API_KEY") or None

# Local persistence (SQLite cache). Keep deterministic across working directories.
CACHE_DB_PATH: str = str(repo_root / "outputs" / "cache.db")

# Request-level orchestration timeout (seconds).
REQUEST_TIMEOUT_SECONDS: float = 180.0

# Max concurrent research requests. Each request fans out many blocking fetches
# onto the thread pool; without a cap, a burst of requests starves the pool and
# every in-flight request stalls. Queued requests wait outside their own timeout.
MAX_CONCURRENT_REQUESTS: int = int(os.getenv("MAX_CONCURRENT_REQUESTS") or "8")

# Worker count for the asyncio default thread pool (asyncio.to_thread) — bounds
# total threads spawned by concurrent blocking fetches (yfinance, SQLite, Tavily).
THREAD_POOL_WORKERS: int = int(os.getenv("THREAD_POOL_WORKERS") or "32")
