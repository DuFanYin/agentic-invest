"""
Central config — load .env once, expose typed constants.
All other modules import from here instead of calling os.getenv directly.
"""

from __future__ import annotations

import os
from pathlib import Path


def _load_env() -> None:
    if os.getenv("OPENROUTER_API_KEY"):
        return
    try:
        from dotenv import load_dotenv  # type: ignore[import]
        directory = Path(__file__).resolve().parent
        for _ in range(8):
            candidate = directory / ".env"
            if candidate.is_file():
                load_dotenv(candidate)
                return
            parent = directory.parent
            if parent == directory:
                break
            directory = parent
    except ImportError:
        pass


_load_env()

OPENROUTER_API_KEY: str | None = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL: str = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_HTTP_REFERER: str | None = os.getenv("OPENROUTER_HTTP_REFERER") or None
OPENROUTER_APP_TITLE: str | None = os.getenv("OPENROUTER_APP_TITLE") or None

TAVILY_API_KEY: str | None = os.getenv("TAVILY_API_KEY") or None
