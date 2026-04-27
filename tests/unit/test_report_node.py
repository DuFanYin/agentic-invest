"""Unit tests for report_verification_node — LLM mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.server.agents.report_verification import report_verification_node
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
    "## Bull / Base / Bear Thesis",
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
        Scenario(name="Bull case", description="Upside.", score=0.3, evidence_ids=["ev_001"]),
        Scenario(name="Base case", description="Base.", score=0.5, evidence_ids=["ev_001"]),
        Scenario(name="Bear case", description="Downside.", score=0.2, evidence_ids=["ev_001"]),
    ]


def _fa(evidence_ids=None) -> dict:
    ids = evidence_ids or ["ev_001", "ev_002"]
    return {
        "agent": "fundamental_analysis",
        "claims": [{"statement": "Stable margins.", "confidence": "high", "evidence_ids": ids}],
        "business_quality": {"view": "stable", "drivers": ["brand"]},
        "financials": {"profitability_trend": "improving", "cash_flow_quality": "high"},
        "valuation": {"relative_multiple_view": "near median", "simplified_dcf_view": "fair"},
        "fundamental_risks": [{"name": "Margin risk", "impact": "medium", "signal": "GM declining", "evidence_ids": ids}],
        "missing_fields": [],
        "metrics": {},
    }


def _ms(evidence_ids=None) -> dict:
    ids = evidence_ids or ["ev_002", "ev_003"]
    return {
        "agent": "market_sentiment",
        "claims": [{"statement": "Sentiment positive.", "confidence": "medium", "evidence_ids": ids}],
        "news_sentiment": {"direction": "positive", "confidence": "medium"},
        "price_action": {"trend": "upward", "return_30d_pct": 3.1, "volatility": "medium"},
        "market_narrative": {"summary": "Investors optimistic.", "crowding_risk": "low"},
        "sentiment_risks": [{"name": "Reversal risk", "impact": "low", "signal": "weak guidance", "evidence_ids": ids}],
        "missing_fields": [],
    }


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


def _mock_llm_markdown(report: str | None = None, raises: Exception | None = None):
    if raises:
        return patch(
            "src.server.agents.report_verification._llm_markdown",
            side_effect=raises,
        )
    text = report or "\n".join(_REQUIRED_SECTIONS) + "\n\nNot financial advice."
    return patch(
        "src.server.agents.report_verification._llm_markdown",
        return_value=text,
    )


# ── output keys ────────────────────────────────────────────────────────────

def test_returns_report_markdown():
    with _mock_llm_markdown():
        result = report_verification_node(_state())
    assert "report_markdown" in result
    assert isinstance(result["report_markdown"], str)
    assert len(result["report_markdown"]) > 50


def test_returns_report_json():
    with _mock_llm_markdown():
        result = report_verification_node(_state())
    rj = result["report_json"]
    assert "intent" in rj
    assert "evidence" in rj
    assert "scenarios" in rj
    assert "validation" in rj


def test_returns_validation_result():
    with _mock_llm_markdown():
        result = report_verification_node(_state())
    vr = result["validation_result"]
    assert hasattr(vr, "is_valid")
    assert hasattr(vr, "errors")
    assert hasattr(vr, "warnings")


# ── section headers ────────────────────────────────────────────────────────

def test_llm_report_contains_required_sections():
    with _mock_llm_markdown():
        result = report_verification_node(_state())
    md = result["report_markdown"]
    for section in _REQUIRED_SECTIONS:
        assert section in md, f"Missing section: {section}"


def test_disclaimer_says_not_financial_advice():
    with _mock_llm_markdown():
        result = report_verification_node(_state())
    assert "Not financial advice" in result["report_markdown"]


# ── validation ─────────────────────────────────────────────────────────────

def test_valid_state_produces_no_errors():
    with _mock_llm_markdown():
        result = report_verification_node(_state())
    assert result["validation_result"].errors == []
    assert result["validation_result"].is_valid is True


def test_validation_errors_appended_to_report():
    # Use a scenario that doesn't sum to 1 to trigger a validation error
    bad_scenarios = [
        Scenario(name="Bull", description=".", score=0.5, evidence_ids=["ev_001"]),
        Scenario(name="Base", description=".", score=0.5, evidence_ids=["ev_001"]),
        Scenario(name="Bear", description=".", score=0.5, evidence_ids=["ev_001"]),
    ]
    with _mock_llm_markdown():
        result = report_verification_node(_state(scenarios=bad_scenarios))
    assert "Validation Warnings" in result["report_markdown"]
    assert result["validation_result"].is_valid is False


def test_missing_fields_produce_warnings():
    fa = _fa()
    fa["missing_fields"] = ["eps", "free_cash_flow"]
    with _mock_llm_markdown():
        result = report_verification_node(_state(fa=fa))
    assert len(result["validation_result"].warnings) >= 1


# ── fallback on LLM failure ────────────────────────────────────────────────

def test_fallback_when_llm_raises():
    with _mock_llm_markdown(raises=RuntimeError("all models failed")):
        result = report_verification_node(_state())
    md = result["report_markdown"]
    assert "# Executive Summary" in md
    assert "## Disclaimer" in md
    assert "Not financial advice" in md


def test_fallback_has_all_required_sections():
    with _mock_llm_markdown(raises=Exception("err")):
        result = report_verification_node(_state())
    md = result["report_markdown"]
    for section in _REQUIRED_SECTIONS:
        assert section in md, f"Fallback missing section: {section}"


def test_fallback_when_no_evidence():
    with _mock_llm_markdown():
        result = report_verification_node(_state(evidence=[]))
    assert "report_markdown" in result
    assert len(result["report_markdown"]) > 0


# ── report_json structure ──────────────────────────────────────────────────

def test_report_json_scenarios_are_dicts():
    with _mock_llm_markdown():
        result = report_verification_node(_state())
    for s in result["report_json"]["scenarios"]:
        assert isinstance(s, dict)
        assert "name" in s
        assert "score" in s


def test_report_json_evidence_are_dicts():
    with _mock_llm_markdown():
        result = report_verification_node(_state())
    for e in result["report_json"]["evidence"]:
        assert isinstance(e, dict)
        assert "id" in e


# ── statuses unchanged when empty ─────────────────────────────────────────

def test_empty_statuses_returned_unchanged():
    with _mock_llm_markdown():
        result = report_verification_node(_state())
    assert result["agent_statuses"] == []
