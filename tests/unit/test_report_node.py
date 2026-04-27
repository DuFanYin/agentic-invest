"""Unit tests for report_verification_node — LLM injected directly."""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from src.server.agents.report_verification import report_verification_node
from src.server.models.analysis import FundamentalAnalysis, MarketSentiment
from src.server.models.evidence import Evidence
from src.server.models.intent import ResearchIntent
from src.server.models.scenario import Scenario

_REQUIRED_SECTIONS = [
    "# Executive Summary",
    "## Company Overview",
    "## Key Evidence",
    "## Fundamental Analysis",
    "## Market Sentiment",
    "## Valuation View",
    "## Risk Analysis",
    "## Future Scenarios",
    "## Scenario Implications",
    "## What To Watch Next",
    "## Sources",
    "## Disclaimer",
]


def _evidence(n: int = 3) -> list[Evidence]:
    return [
        Evidence(
            id=f"ev_{i:03d}",
            source_type="financial_api",
            title=f"Evidence {i}",
            url=f"https://example.com/{i}",
            summary=f"Summary {i}",
            reliability="high",
            retrieved_at="2026-01-01T00:00:00Z",
        )
        for i in range(1, n + 1)
    ]


def _scenarios() -> list[Scenario]:
    return [
        Scenario(name="Rate plateau stalls growth", description="Downside.", tags=["bearish-1"],
                 probability=0.2, drivers=["d"], triggers=["t"], signals=["s"], evidence_ids=["ev_001"]),
        Scenario(name="AI capex supercycle", description="Base.", tags=["neutral"],
                 probability=0.5, drivers=["d"], triggers=["t"], signals=["s"], evidence_ids=["ev_001"]),
        Scenario(name="Margin expansion", description="Upside.", tags=["bullish-1"],
                 probability=0.3, drivers=["d"], triggers=["t"], signals=["s"], evidence_ids=["ev_001"]),
    ]


def _fa(evidence_ids=None) -> FundamentalAnalysis:
    ids = evidence_ids or ["ev_001", "ev_002"]
    return FundamentalAnalysis.model_validate({
        "claims": [{"statement": "Stable margins.", "confidence": "high", "evidence_ids": ids}],
        "business_quality": {"view": "stable", "drivers": ["brand"]},
        "financials": {"profitability_trend": "improving", "cash_flow_quality": "high"},
        "valuation": {"relative_multiple_view": "near median", "simplified_dcf_view": "fair"},
        "fundamental_risks": [{"name": "Margin risk", "impact": "medium", "signal": "GM declining", "evidence_ids": ids}],
        "missing_fields": [],
        "metrics": {},
    })


def _ms(evidence_ids=None) -> MarketSentiment:
    ids = evidence_ids or ["ev_002", "ev_003"]
    return MarketSentiment.model_validate({
        "claims": [{"statement": "Sentiment positive.", "confidence": "medium", "evidence_ids": ids}],
        "news_sentiment": {"direction": "positive", "confidence": "medium"},
        "price_action": {"trend": "upward", "return_30d_pct": 3.1, "volatility": "medium"},
        "market_narrative": {"summary": "Investors optimistic.", "crowding_risk": "low"},
        "sentiment_risks": [{"name": "Reversal risk", "impact": "low", "signal": "weak guidance", "evidence_ids": ids}],
        "missing_fields": [],
    })


def _state(evidence=None, fa=None, ms=None, scenarios=None):
    return {
        "query": "Analyse AAPL",
        "intent": ResearchIntent(ticker="AAPL", subjects=["Apple"], scope="company", time_horizon="3 years"),
        "evidence": evidence if evidence is not None else _evidence(),
        "fundamental_analysis": fa if fa is not None else _fa(),
        "market_sentiment": ms if ms is not None else _ms(),
        "scenarios": scenarios if scenarios is not None else _scenarios(),
        "agent_statuses": [],
    }


def _mock_llm(report: str | None = None, raises: Exception | None = None):
    text = report or "\n".join(_REQUIRED_SECTIONS) + "\n\nNot financial advice."
    llm = MagicMock()
    if raises:
        llm.complete_text = AsyncMock(side_effect=raises)
    else:
        llm.complete_text = AsyncMock(return_value=text)
    return llm


def _run(coro):
    return asyncio.run(coro)


# ── section headers ────────────────────────────────────────────────────────

def test_llm_report_contains_required_sections():
    result = _run(report_verification_node(_state(), llm=_mock_llm()))
    md = result["report_markdown"]
    for section in _REQUIRED_SECTIONS:
        assert section in md, f"Missing section: {section}"


def test_disclaimer_says_not_financial_advice():
    result = _run(report_verification_node(_state(), llm=_mock_llm()))
    assert "Not financial advice" in result["report_markdown"]


# ── validation ─────────────────────────────────────────────────────────────

def test_valid_state_produces_no_errors():
    result = _run(report_verification_node(_state(), llm=_mock_llm()))
    assert result["validation_result"].errors == []
    assert result["validation_result"].is_valid is True


def test_validation_errors_appended_to_report():
    bad_scenarios = [
        Scenario(name="Upside", description=".", tags=["bullish-1"], probability=0.5,
                 drivers=["d"], triggers=["t"], signals=["s"], evidence_ids=["ev_001"]),
        Scenario(name="Base", description=".", tags=["neutral"], probability=0.5,
                 drivers=["d"], triggers=["t"], signals=["s"], evidence_ids=["ev_001"]),
        Scenario(name="Downside", description=".", tags=["bearish-1"], probability=0.5,
                 drivers=["d"], triggers=["t"], signals=["s"], evidence_ids=["ev_001"]),
    ]
    result = _run(report_verification_node(_state(scenarios=bad_scenarios), llm=_mock_llm()))
    assert "Validation Warnings" in result["report_markdown"]
    assert result["validation_result"].is_valid is False


def test_missing_fields_produce_warnings():
    fa = _fa().model_copy(update={"missing_fields": ["eps", "free_cash_flow"]})
    result = _run(report_verification_node(_state(fa=fa), llm=_mock_llm()))
    assert len(result["validation_result"].warnings) >= 1


# ── raises on LLM failure (no stub fallback) ──────────────────────────────

def test_raises_when_llm_raises():
    with pytest.raises(RuntimeError, match="report_verification"):
        _run(report_verification_node(_state(), llm=_mock_llm(raises=RuntimeError("all models failed"))))


def test_raises_when_no_evidence():
    with pytest.raises(RuntimeError, match="report_verification"):
        _run(report_verification_node(_state(evidence=[]), llm=_mock_llm()))


# ── statuses unchanged when empty ─────────────────────────────────────────

def test_empty_statuses_returned_unchanged():
    result = _run(report_verification_node(_state(), llm=_mock_llm()))
    assert result["agent_statuses"] == []


# ── open_questions re-route signal ────────────────────────────────────────

@pytest.mark.parametrize(
    ("fa", "has_open_questions"),
    [
        (None, False),
        (_fa(evidence_ids=["ev_999"]), True),
    ],
)
def test_open_questions_reroute_signal(fa, has_open_questions):
    result = _run(report_verification_node(_state(fa=fa), llm=_mock_llm()))
    if has_open_questions:
        assert len(result["open_questions"]) >= 1
        assert all("report_verification" in q for q in result["open_questions"])
    else:
        assert result["open_questions"] == []
