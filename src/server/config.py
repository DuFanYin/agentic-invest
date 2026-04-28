"""
Central config — load .env once, expose typed constants.
All other modules import from here instead of calling os.getenv directly.
"""

from __future__ import annotations

import os
from pathlib import Path


if not os.getenv("LLM_API_KEY"):
    try:
        from dotenv import load_dotenv  # type: ignore[import]
        directory = Path(__file__).resolve().parent
        for _ in range(8):
            candidate = directory / ".env"
            if candidate.is_file():
                load_dotenv(candidate)
                break
            parent = directory.parent
            if parent == directory:
                break
            directory = parent
    except ImportError:
        pass

# Required
LLM_PROVIDER: str = (os.getenv("LLM_PROVIDER") or "openrouter").strip().lower()
LLM_API_KEY: str | None = os.getenv("LLM_API_KEY") or None

# Derived runtime defaults (not environment-facing)
LLM_BASE_URL: str = "https://api.openai.com/v1" if LLM_PROVIDER == "openai" else "https://openrouter.ai/api/v1"
LLM_HTTP_REFERER: str | None = None
LLM_APP_TITLE: str | None = None

TAVILY_API_KEY: str | None = os.getenv("TAVILY_API_KEY") or None
FRED_API_KEY: str | None = os.getenv("FRED_API_KEY") or None

# Local persistence (SQLite cache). Keep deterministic across working directories.
CACHE_DB_PATH: str = "outputs/cache.db"
