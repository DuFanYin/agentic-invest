"""Integration tests: FastAPI endpoints (LLM calls mocked)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from src.server import shutdown
from src.server.agents.orchestrator import OrchestratorAgent
from src.server.main import app
from src.server.services.llm_provider import LLMClient


def _mock_llm(
    intent_json: str | None = None,
    fa_json: str | None = None,
    macro_json: str | None = None,
    ms_json: str | None = None,
    scenarios_json: str | None = None,
    debate_json: str | None = None,
    report_md: str | None = None,
) -> MagicMock:
    """Single mock LLM that returns appropriate responses for each node."""
    _intent = intent_json or json.dumps(
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
    _fa = fa_json or json.dumps(
        {
            "claims": [{"statement": "Strong margins.", "confidence": "high", "evidence_ids": ["ev_001"]}],
            "business_quality": {"view": "stable", "drivers": ["brand"]},
            "financials": {"profitability_trend": "improving", "cash_flow_quality": "high"},
            "valuation": {"relative_multiple_view": "near median", "simplified_dcf_view": "fair"},
            "fundamental_risks": [
                {"name": "Competition", "impact": "medium", "signal": "market share", "evidence_ids": ["ev_001"]}
            ],
            "missing_fields": [],
        }
    )
    _ms = ms_json or json.dumps(
        {
            "claims": [{"statement": "Positive sentiment.", "confidence": "medium", "evidence_ids": ["ev_001"]}],
            "news_sentiment": {"direction": "positive", "confidence": "medium"},
            "price_action": {"trend": "upward", "return_30d_pct": 3.1, "volatility": "medium"},
            "market_narrative": {"summary": "Investors optimistic.", "crowding_risk": "low"},
            "sentiment_risks": [
                {"name": "Reversal", "impact": "low", "signal": "guidance", "evidence_ids": ["ev_001"]}
            ],
            "missing_fields": [],
        }
    )
    _macro = macro_json or json.dumps(
        {
            "macro_view": "Disinflation continues while growth remains resilient.",
            "macro_drivers": ["Fed policy near neutral", "credit spreads contained"],
            "macro_risks": [{"name": "Sticky inflation", "impact": "medium", "signal": "CPI re-acceleration"}],
            "macro_signals": ["FEDFUNDS trend", "10Y yield direction"],
            "rate_environment": "stable",
            "growth_environment": "expanding",
            "missing_fields": [],
        }
    )
    _scenarios = scenarios_json or json.dumps(
        [
            {
                "name": "AI capex supercycle",
                "description": "Demand accelerates.",
                "raw_probability": 0.5,
                "drivers": ["AI"],
                "triggers": ["capex"],
                "signals": ["orders"],
                "evidence_ids": ["ev_001"],
                "tags": ["bullish-2", "ai-demand"],
            },
            {
                "name": "Rate plateau stalls growth",
                "description": "Headwinds persist.",
                "raw_probability": 0.3,
                "drivers": ["rates"],
                "triggers": ["Fed"],
                "signals": ["yields"],
                "evidence_ids": ["ev_001"],
                "tags": ["bearish-1", "rate-sensitive"],
            },
            {
                "name": "Regulatory crackdown",
                "description": "Policy risk.",
                "raw_probability": 0.2,
                "drivers": ["policy"],
                "triggers": ["legislation"],
                "signals": ["hearings"],
                "evidence_ids": ["ev_001"],
                "tags": ["bearish-2", "policy-risk"],
            },
        ]
    )
    _debate = debate_json or json.dumps(
        {
            "debate_summary": "Bull and bear views offset; only mild probability shifts applied.",
            "probability_adjustments": [
                {
                    "scenario_name": "AI capex supercycle",
                    "before": 0.5,
                    "after": 0.48,
                    "delta": -0.02,
                    "reason": "Valuation sensitivity to rates remains elevated.",
                    "evidence_refs": ["ev_001"],
                }
            ],
            "calibrated_scenarios": [
                {"name": "AI capex supercycle", "probability": 0.48, "tags": ["bullish-2", "ai-demand"]},
                {"name": "Rate plateau stalls growth", "probability": 0.32, "tags": ["bearish-1", "rate-sensitive"]},
                {"name": "Regulatory crackdown", "probability": 0.20, "tags": ["bearish-2", "policy-risk"]},
            ],
            "confidence": "medium",
            "debate_flags": [],
        }
    )
    _report = report_md or (
        "# Executive Summary\n## Company Overview\n## Key Evidence\n"
        "## Fundamental Analysis\n## Macro Environment\n## Market Sentiment\n## Valuation View\n"
        "## Risk Analysis\n## Future Scenarios\n## Scenario Debate & Calibration\n## Scenario Implications\n"
        "## What To Watch Next\n## Sources\n## Disclaimer\nNot financial advice."
    )

    llm = MagicMock(spec=LLMClient)
    call_count = {"n": 0}

    async def _call_with_retry(prompt: str, **kw) -> str:
        call_count["n"] += 1
        # Route by distinctive schema markers in each node's prompt
        if "DEBATE STRUCTURE" in prompt or "probability_adjustments" in prompt:  # scenario_debate schema
            return _debate
        if "raw_probability" in prompt:  # scenario_scoring schema
            return _scenarios
        if "rate_environment" in prompt and "growth_environment" in prompt:  # macro_analysis schema
            return _macro
        if "business_quality" in prompt:  # fundamental_analysis schema
            return _fa
        if "news_sentiment" in prompt:  # market_sentiment schema
            return _ms
        return _fa  # fallback

    async def _complete(prompt: str, **kw) -> str:
        return _intent

    async def _complete_text(prompt: str, **kw) -> str:
        return _report

    llm.call_with_retry = AsyncMock(side_effect=_call_with_retry)
    llm.complete = AsyncMock(side_effect=_complete)
    llm.complete_text = AsyncMock(side_effect=_complete_text)
    return llm


def _patched_orchestrator() -> OrchestratorAgent:
    """Build a real OrchestratorAgent with a mock LLM injected at graph level."""
    return OrchestratorAgent(llm_client=_mock_llm())


# ── health ─────────────────────────────────────────────────────────────────


def test_health_endpoint() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_lifespan_clears_then_sets_shutdown_flag() -> None:
    # Simulate stale state from a prior lifecycle.
    shutdown.event.set()
    assert shutdown.event.is_set() is True

    with TestClient(app) as client:
        # Startup should clear stale shutdown state so new requests can run.
        assert shutdown.event.is_set() is False
        response = client.get("/health")
        assert response.status_code == 200

    # Teardown should set the flag to interrupt in-flight waits/streams.
    assert shutdown.event.is_set() is True


# ── research endpoint ──────────────────────────────────────────────────────


def test_research_endpoint_returns_valid_response() -> None:
    with patch("src.server.routes.research.OrchestratorAgent", return_value=_patched_orchestrator()):
        client = TestClient(app)
        response = client.post("/research", json={"query": "Analyse NVDA long-term"})

    assert response.status_code == 200
    data = response.json()
    assert data["report_markdown"]
    assert isinstance(data["scenarios"], list)
    assert len(data["scenarios"]) >= 3
    total = sum(s["probability"] for s in data["scenarios"])
    assert abs(total - 1.0) < 1e-5
    assert data["validation_result"]["is_valid"] is True
    assert data["validation_result"]["errors"] == []


def test_research_stream_emits_final_and_done() -> None:
    with patch("src.server.routes.research.OrchestratorAgent", return_value=_patched_orchestrator()):
        client = TestClient(app)
        response = client.post("/research/stream", json={"query": "Analyse NVDA long-term"})

    assert response.status_code == 200
    text = response.text
    assert "event: agent_status" in text
    assert "event: final" in text
    assert "event: done" in text


def test_research_stream_shutdown_emits_error_then_done() -> None:
    orchestrator = MagicMock()

    async def _run_stream(_request):
        yield {"type": "agent_status", "payload": []}
        yield {"type": "final", "payload": {"report_markdown": "ok"}}

    orchestrator.run_stream = _run_stream

    with patch("src.server.routes.research.OrchestratorAgent", return_value=orchestrator):
        with patch("src.server.routes.research.shutdown.is_set", return_value=True):
            client = TestClient(app)
            response = client.post("/research/stream", json={"query": "Analyse NVDA long-term"})

    assert response.status_code == 200
    text = response.text
    assert "event: error" in text
    assert "server shutting down" in text
    assert "event: done" in text
