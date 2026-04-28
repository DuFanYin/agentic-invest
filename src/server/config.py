"""
Central config — load .env once, expose typed constants.
All other modules import from here instead of calling os.getenv directly.
"""

from __future__ import annotations

import os
from pathlib import Path


try:
    from dotenv import load_dotenv  # type: ignore[import]
except ImportError as exc:
    raise RuntimeError("python-dotenv is required but not installed; cannot load .env") from exc

repo_root = Path(__file__).resolve().parents[2]
env_path = repo_root / ".env"
if not env_path.is_file():
    raise RuntimeError(f"Missing .env file at fixed path: {env_path}")
load_dotenv(env_path)

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
CACHE_DB_PATH: str = str(repo_root / "outputs" / "cache.db")

# Request-level orchestration timeout (seconds).
REQUEST_TIMEOUT_SECONDS: float = 180.0
