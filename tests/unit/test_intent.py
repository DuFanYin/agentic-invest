"""Unit test: intent parsing falls back gracefully when LLM is unavailable."""

from unittest.mock import MagicMock

from src.server.agents.orchestrator import OrchestratorAgent
from src.server.services.openrouter import OpenRouterClient


def test_parse_intent_from_query_fallback() -> None:
    """When the LLM client raises, _parse_intent returns a sane fallback."""
    broken_client = MagicMock(spec=OpenRouterClient)
    broken_client.complete.side_effect = RuntimeError("no key")

    agent = OrchestratorAgent(llm_client=broken_client)
    intent = agent._parse_intent("Analyse NVDA for long-term investment")

    assert intent.intent == "investment_research"
    assert intent.subjects  # non-empty fallback
