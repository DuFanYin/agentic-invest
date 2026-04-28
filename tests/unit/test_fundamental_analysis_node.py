"""Unit tests for fundamental_analysis_node — LLM injected directly."""

from __future__ import annotations

import asyncio
import pytest
import json
from unittest.mock import AsyncMock, MagicMock

from src.server.agents.fundamental_analysis import fundamental_analysis_node
from src.server.models.analysis import FundamentalAnalysis, MetricsBlock, NormalizedData
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
    raw_metrics = metrics if metrics is not None else _metrics()
    return {
        "query": "Analyse AAPL",
        "intent": intent or ResearchIntent(ticker="AAPL", subjects=["Apple"], scope="company"),
        "evidence": evidence if evidence is not None else _evidence(),
        "normalized_data": NormalizedData(
            query="Analyse AAPL",
            metrics=MetricsBlock(
                ttm=raw_metrics.get("ttm", {}),
                three_year_avg=raw_metrics.get("three_year_avg", {}),
                latest_quarter=raw_metrics.get("latest_quarter", {}),
            ),
            missing_fields=missing_fields or [],
        ),
        "agent_statuses": [],
    }


def _mock_llm(response: dict | None = None, raises: Exception | None = None):
    llm = MagicMock()
    if raises:
        llm.call_with_retry = AsyncMock(side_effect=raises)
    else:
        llm.call_with_retry = AsyncMock(return_value=json.dumps(response or _llm_response()))
    return llm


def _run(coro):
    return asyncio.run(coro)


def _fa(result) -> FundamentalAnalysis:
    return result["fundamental_analysis"]


# ── output shape ───────────────────────────────────────────────────────────

def test_result_shape_and_core_fields():
    result = _run(fundamental_analysis_node(_state(), llm=_mock_llm()))
    fa = _fa(result)
    assert isinstance(fa, FundamentalAnalysis)
    claims = fa.claims
    assert len(claims) >= 1
    valid = {"high", "medium", "low"}
    for claim in claims:
        assert isinstance(claim.evidence_ids, list)
        assert len(claim.evidence_ids) >= 1
        assert claim.confidence in valid
    assert fa.business_quality.view in {"strong", "stable", "weak", "deteriorating"}
    raw = _metrics()
    assert fa.metrics.get("ttm") == raw["ttm"]
    assert fa.metrics.get("three_year_avg") == raw["three_year_avg"]
    assert fa.metrics.get("latest_quarter") == raw["latest_quarter"]


# ── raises on LLM failure (no stub fallback) ──────────────────────────────

def test_raises_when_llm_raises():
    with pytest.raises(RuntimeError, match="fundamental_analysis"):
        _run(fundamental_analysis_node(_state(), llm=_mock_llm(raises=RuntimeError("all models exhausted"))))


def test_raises_when_no_evidence():
    with pytest.raises(RuntimeError, match="fundamental_analysis"):
        _run(fundamental_analysis_node(_state(evidence=[]), llm=_mock_llm()))


