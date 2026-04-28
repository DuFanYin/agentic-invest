"""Unit tests for report_finalize_node — LLM injected directly."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from src.server.agents.report_finalize import report_finalize_node
from src.server.models.analysis import (
    BusinessQuality,
    FundamentalAnalysis,
    MacroAnalysis,
    MarketNarrative,
    MarketSentiment,
    NewsSentiment,
    ScenarioDebate,
    Valuation,
)
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
        Scenario(
            name="Rate plateau stalls growth",
            description="Downside.",
            tags=["bearish-1"],
            probability=0.2,
            drivers=["d"],
            triggers=["t"],
            evidence_ids=["ev_001"],
        ),
        Scenario(
            name="AI capex supercycle",
            description="Base.",
            tags=["neutral"],
            probability=0.5,
            drivers=["d"],
            triggers=["t"],
            evidence_ids=["ev_001"],
        ),
        Scenario(
            name="Margin expansion",
            description="Upside.",
            tags=["bullish-1"],
            probability=0.3,
            drivers=["d"],
            triggers=["t"],
            evidence_ids=["ev_001"],
        ),
    ]


def _fa(evidence_ids=None) -> FundamentalAnalysis:
    ids = evidence_ids or ["ev_001", "ev_002"]
    return FundamentalAnalysis.model_validate(
        {
            "claims": [
                {
                    "statement": "Stable margins.",
                    "confidence": "high",
                    "evidence_ids": ids,
                }
            ],
            "business_quality": {"view": "stable", "drivers": ["brand"]},
            "financials": {
                "profitability_trend": "improving",
                "cash_flow_quality": "high",
            },
            "valuation": {
                "relative_multiple_view": "near median",
                "simplified_dcf_view": "fair",
            },
            "fundamental_risks": [
                {
                    "name": "Margin risk",
                    "impact": "medium",
                    "signal": "GM declining",
                    "evidence_ids": ids,
                }
            ],
            "missing_fields": [],
            "metrics": {},
        }
    )


def _ms(evidence_ids=None) -> MarketSentiment:
    ids = evidence_ids or ["ev_002", "ev_003"]
    return MarketSentiment.model_validate(
        {
            "claims": [
                {
                    "statement": "Sentiment positive.",
                    "confidence": "medium",
                    "evidence_ids": ids,
                }
            ],
            "news_sentiment": {"direction": "positive", "confidence": "medium"},
            "price_action": {
                "trend": "upward",
                "return_30d_pct": 3.1,
                "volatility": "medium",
            },
            "market_narrative": {
                "summary": "Investors optimistic.",
                "crowding_risk": "low",
            },
            "sentiment_risks": [
                {
                    "name": "Reversal risk",
                    "impact": "low",
                    "signal": "weak guidance",
                    "evidence_ids": ids,
                }
            ],
            "missing_fields": [],
        }
    )


def _state(evidence=None, fa=None, ms=None, scenarios=None):
    return {
        "query": "Analyse AAPL",
        "intent": ResearchIntent(
            ticker="AAPL", subjects=["Apple"], scope="company", time_horizon="3 years"
        ),
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
    result = _run(report_finalize_node(_state(), llm=_mock_llm()))
    md = result["report_markdown"]
    for section in _REQUIRED_SECTIONS:
        assert section in md, f"Missing section: {section}"


# ── validation ─────────────────────────────────────────────────────────────


def test_validation_errors_appended_to_report():
    bad_scenarios = [
        Scenario(
            name="Upside",
            description=".",
            tags=["bullish-1"],
            probability=0.5,
            drivers=["d"],
            triggers=["t"],
            evidence_ids=["ev_001"],
        ),
        Scenario(
            name="Base",
            description=".",
            tags=["neutral"],
            probability=0.5,
            drivers=["d"],
            triggers=["t"],
            evidence_ids=["ev_001"],
        ),
        Scenario(
            name="Downside",
            description=".",
            tags=["bearish-1"],
            probability=0.5,
            drivers=["d"],
            triggers=["t"],
            evidence_ids=["ev_001"],
        ),
    ]
    result = _run(
        report_finalize_node(_state(scenarios=bad_scenarios), llm=_mock_llm())
    )
    assert "Validation Errors" in result["report_markdown"]
    assert result["validation_result"].is_valid is False


# ── LLM failure produces placeholder sections, not a crash ───────────────


def test_llm_failure_still_returns_report():
    # Section-by-section rendering: LLM failures degrade to placeholder text
    result = _run(
        report_finalize_node(
            _state(), llm=_mock_llm(raises=RuntimeError("all models failed"))
        )
    )
    md = result["report_markdown"]
    assert isinstance(md, str) and len(md) > 50
    # Scenarios section is Python-rendered — always present when scenarios exist
    assert "Future Scenarios" in md
    # Narrative sections degrade to placeholder text
    assert "*Section unavailable.*" in md


def test_raises_when_no_evidence():
    with pytest.raises(RuntimeError, match="report_finalize"):
        _run(report_finalize_node(_state(evidence=[]), llm=_mock_llm()))


# ── degraded node disclosure ───────────────────────────────────────────────


def _degraded_fa() -> FundamentalAnalysis:
    return FundamentalAnalysis(
        claims=[],
        business_quality=BusinessQuality(view="stable"),
        valuation=Valuation(relative_multiple_view="unavailable"),
        degraded=True,
    )


def _degraded_macro() -> MacroAnalysis:
    return MacroAnalysis(macro_view="Macro analysis unavailable.", degraded=True)


def _degraded_ms() -> MarketSentiment:
    return MarketSentiment(
        news_sentiment=NewsSentiment(direction="neutral"),
        market_narrative=MarketNarrative(summary="Sentiment analysis unavailable."),
        degraded=True,
    )


def test_single_degraded_node_produces_warning():
    state = _state(fa=_degraded_fa())
    result = _run(report_finalize_node(state, llm=_mock_llm()))
    warnings = result["validation_result"].warnings
    assert any("fundamental_analysis unavailable" in w for w in warnings)


def test_partial_degraded_still_generates_report():
    state = _state(fa=_degraded_fa())
    result = _run(report_finalize_node(state, llm=_mock_llm()))
    assert isinstance(result["report_markdown"], str)
    assert len(result["report_markdown"]) > 50


def test_all_three_degraded_raises():
    state = _state(fa=_degraded_fa(), ms=_degraded_ms())
    state["macro_analysis"] = _degraded_macro()
    with pytest.raises(RuntimeError, match="all three analysis nodes degraded"):
        _run(report_finalize_node(state, llm=_mock_llm()))


def test_debate_degraded_produces_warning():
    state = _state()
    state["scenario_debate"] = ScenarioDebate(
        debate_summary="Debate unavailable.",
        calibrated_scenarios=[
            {"name": s.name, "probability": s.probability} for s in _scenarios()
        ],
        confidence="low",
        debate_flags=["debate_degraded"],
        degraded=True,
    )
    result = _run(report_finalize_node(state, llm=_mock_llm()))
    warnings = result["validation_result"].warnings
    assert any("scenario_debate unavailable" in w for w in warnings)


def test_no_degraded_produces_no_degraded_warnings():
    result = _run(report_finalize_node(_state(), llm=_mock_llm()))
    warnings = result["validation_result"].warnings
    assert not any("unavailable" in w for w in warnings)
