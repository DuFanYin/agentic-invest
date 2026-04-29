"""Unit test: intent parsing falls back gracefully when LLM is unavailable."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.server.agents.planning_agent import plan
from src.server.services.llm_provider import LLMClient


def test_intent_from_query_planner_fallback() -> None:
    """When the LLM client raises, plan() returns a sane fallback intent."""
    broken_client = MagicMock(spec=LLMClient)
    broken_client.complete = AsyncMock(side_effect=RuntimeError("no key"))

    result = asyncio.run(plan("Analyse NVDA for long-term investment", broken_client))

    assert result.intent.intent == "investment_research"
    assert result.intent.subjects  # non-empty fallback
