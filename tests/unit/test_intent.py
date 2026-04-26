from src.server.agents.orchestrator import OrchestratorAgent


def test_parse_intent_from_query() -> None:
    intent = OrchestratorAgent()._parse_intent("Analyze NVDA for long-term investment")

    assert intent.intent == "investment_research"
    assert intent.subjects
