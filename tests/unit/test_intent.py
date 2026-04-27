"""Unit test: intent parsing falls back gracefully when LLM is unavailable."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.server.agents.planning_agent import parse_intent as _parse_intent
from src.server.services.openrouter import OpenRouterClient


def test_parse_intent_from_query_fallback() -> None:
    """When the LLM client raises, _parse_intent returns a sane fallback."""
    broken_client = MagicMock(spec=OpenRouterClient)
    broken_client.complete = AsyncMock(side_effect=RuntimeError("no key"))

    intent = asyncio.run(_parse_intent("Analyse NVDA for long-term investment", broken_client))

    assert intent.intent == "investment_research"
    assert intent.subjects  # non-empty fallback
