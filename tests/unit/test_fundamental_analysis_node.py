"""Unit tests for fundamental_analysis_node — LLM mocked."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from src.server.agents.fundamental_analysis import fundamental_analysis_node
from src.server.models.evidence import Evidence
from src.server.models.intent import ResearchIntent


def _evidence(n: int = 3) -> list[Evidence]:
    return [
        Evidence(
            id=f"ev_{i:03d}",
            source_type="financial_api",
            title=f"Evidence {i}",
            summary=f"Summary {i}",
            reliability="high",
            retrieved_at="2026-01-01T00:00:00Z",
        )
        for i in range(1, n + 1)
    ]


def _metrics() -> dict:
    return {
        "ttm": {"revenue": 400e9, "gross_margin_pct": 44.5, "operating_margin_pct": 30.1},
        "three_year_avg": {"revenue_growth_pct": 10.2},
        "latest_quarter": {"revenue": 94e9, "eps": 1.52},
    }


def _llm_response() -> dict:
    return {
        "claims": [
            {"statement": "Strong margin profile.", "confidence": "high", "evidence_ids": ["ev_001"]},
            {"statement": "Valuation near fair value.", "confidence": "medium", "evidence_ids": ["ev_002"]},
        ],
        "business_quality": {"view": "stable", "drivers": ["brand", "ecosystem"]},
        "financials": {"profitability_trend": "improving", "cash_flow_quality": "high"},
        "valuation": {"relative_multiple_view": "near median", "simplified_dcf_view": "fair"},
        "fundamental_risks": [
            {"name": "Margin pressure", "impact": "medium", "signal": "GM declining", "evidence_ids": ["ev_003"]},
        ],
        "missing_fields": [],
    }


def _state(evidence=None, metrics=None, missing_fields=None, intent=None):
    return {
        "query": "Analyse AAPL",
        "intent": intent or ResearchIntent(ticker="AAPL", subjects=["Apple"], scope="company"),
        "evidence": evidence if evidence is not None else _evidence(),
        "normalized_data": {
            "metrics": metrics if metrics is not None else _metrics(),
            "missing_fields": missing_fields or [],
        },
        "agent_statuses": [],
    }


def _mock_llm(response: dict | None = None, raises: Exception | None = None):
    llm = MagicMock()
    if raises:
        llm.complete.side_effect = raises
    else:
        llm.complete.return_value = json.dumps(response or _llm_response())
    return llm


# ── output shape ───────────────────────────────────────────────────────────

def test_returns_fundamental_analysis_key():
    with patch("src.server.agents.fundamental_analysis._llm", _mock_llm()):
        result = fundamental_analysis_node(_state())
    assert "fundamental_analysis" in result


def test_claims_is_list():
    with patch("src.server.agents.fundamental_analysis._llm", _mock_llm()):
        result = fundamental_analysis_node(_state())
    assert isinstance(result["fundamental_analysis"]["claims"], list)


def test_at_least_one_claim():
    with patch("src.server.agents.fundamental_analysis._llm", _mock_llm()):
        result = fundamental_analysis_node(_state())
    assert len(result["fundamental_analysis"]["claims"]) >= 1


def test_all_claims_have_evidence_ids():
    with patch("src.server.agents.fundamental_analysis._llm", _mock_llm()):
        result = fundamental_analysis_node(_state())
    for claim in result["fundamental_analysis"]["claims"]:
        assert isinstance(claim["evidence_ids"], list)
        assert len(claim["evidence_ids"]) >= 1


def test_all_claims_have_confidence():
    with patch("src.server.agents.fundamental_analysis._llm", _mock_llm()):
        result = fundamental_analysis_node(_state())
    valid = {"high", "medium", "low"}
    for claim in result["fundamental_analysis"]["claims"]:
        assert claim["confidence"] in valid


def test_business_quality_present():
    with patch("src.server.agents.fundamental_analysis._llm", _mock_llm()):
        result = fundamental_analysis_node(_state())
    bq = result["fundamental_analysis"]["business_quality"]
    assert "view" in bq


def test_valuation_present():
    with patch("src.server.agents.fundamental_analysis._llm", _mock_llm()):
        result = fundamental_analysis_node(_state())
    assert "valuation" in result["fundamental_analysis"]


def test_fundamental_risks_is_list():
    with patch("src.server.agents.fundamental_analysis._llm", _mock_llm()):
        result = fundamental_analysis_node(_state())
    assert isinstance(result["fundamental_analysis"]["fundamental_risks"], list)


def test_missing_fields_is_list():
    with patch("src.server.agents.fundamental_analysis._llm", _mock_llm()):
        result = fundamental_analysis_node(_state())
    assert isinstance(result["fundamental_analysis"]["missing_fields"], list)


def test_metrics_attached_from_state():
    with patch("src.server.agents.fundamental_analysis._llm", _mock_llm()):
        result = fundamental_analysis_node(_state())
    assert result["fundamental_analysis"]["metrics"] == _metrics()


def test_llm_used_flag_true_on_success():
    with patch("src.server.agents.fundamental_analysis._llm", _mock_llm()):
        result = fundamental_analysis_node(_state())
    assert result["fundamental_analysis"]["_llm_used"] is True


# ── fallback on LLM failure ────────────────────────────────────────────────

def test_fallback_when_llm_raises():
    with patch("src.server.agents.fundamental_analysis._llm", _mock_llm(raises=RuntimeError("all models exhausted"))):
        result = fundamental_analysis_node(_state())
    fa = result["fundamental_analysis"]
    assert isinstance(fa["claims"], list)
    assert fa["_llm_used"] is False


def test_fallback_still_has_required_keys():
    with patch("src.server.agents.fundamental_analysis._llm", _mock_llm(raises=Exception("err"))):
        result = fundamental_analysis_node(_state())
    fa = result["fundamental_analysis"]
    for key in ("claims", "business_quality", "financials", "valuation", "fundamental_risks", "missing_fields"):
        assert key in fa


def test_fallback_when_no_evidence():
    with patch("src.server.agents.fundamental_analysis._llm", _mock_llm()):
        result = fundamental_analysis_node(_state(evidence=[]))
    fa = result["fundamental_analysis"]
    assert isinstance(fa["claims"], list)
    assert fa["_llm_used"] is False


# ── missing fields propagated ──────────────────────────────────────────────

def test_missing_fields_from_state_in_fallback():
    with patch("src.server.agents.fundamental_analysis._llm", _mock_llm(raises=Exception("err"))):
        result = fundamental_analysis_node(_state(missing_fields=["revenue", "eps"]))
    assert "revenue" in result["fundamental_analysis"]["missing_fields"]


# ── agent_statuses untouched when empty ───────────────────────────────────

def test_empty_statuses_returned_unchanged():
    with patch("src.server.agents.fundamental_analysis._llm", _mock_llm()):
        result = fundamental_analysis_node(_state())
    assert result["agent_statuses"] == []
