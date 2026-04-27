"""Integration tests: FastAPI endpoints (LLM calls mocked)."""

import json
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.server.main import app
from src.server.services.openrouter import OpenRouterClient


def _mock_llm_client() -> MagicMock:
    client = MagicMock(spec=OpenRouterClient)
    client.complete.return_value = json.dumps(
        {
            "intent": "investment_research",
            "subjects": ["NVDA"],
            "scope": "company",
            "ticker": "NVDA",
            "time_horizon": "3 years",
            "risk_level": "medium",
            "required_outputs": ["valuation", "risks", "scenarios"],
        }
    )
    return client


def test_health_endpoint() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_research_endpoint_returns_valid_response() -> None:
    mock_client = _mock_llm_client()
    with patch(
        "src.server.agents.orchestrator.OpenRouterClient", return_value=mock_client
    ):
        client = TestClient(app)
        response = client.post("/research", json={"query": "Analyse NVDA long-term"})

    assert response.status_code == 200
    data = response.json()
    assert data["report_markdown"]
    assert isinstance(data["scenarios"], list)
    assert len(data["scenarios"]) >= 3
    total = sum(s["probability"] for s in data["scenarios"])
    assert abs(total - 1.0) < 1e-6


def test_research_endpoint_validation_is_valid() -> None:
    mock_client = _mock_llm_client()
    with patch(
        "src.server.agents.orchestrator.OpenRouterClient", return_value=mock_client
    ):
        client = TestClient(app)
        response = client.post("/research", json={"query": "Analyse NVDA long-term"})

    data = response.json()
    assert data["validation_result"]["is_valid"] is True
    assert data["validation_result"]["errors"] == []
