"""Unit tests for macro_analysis_node."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.server.agents.macro_analysis import macro_analysis_node
from src.server.models.analysis import MacroAnalysis
from src.server.models.evidence import Evidence
from src.server.models.intent import ResearchIntent


def _run(coro):
    return asyncio.run(coro)


def _evidence(source_types: list[str] | None = None) -> list[Evidence]:
    types = source_types or ["macro_api", "macro_api", "financial_api", "web"]
    return [
        Evidence(
            id=f"ev_{i+1:03d}",
            source_type=t,
            title=f"Evidence {i+1}",
            summary=f"Summary for {t} evidence {i+1}",
            reliability="high",
            retrieved_at="2026-01-01T00:00:00Z",
        )
        for i, t in enumerate(types)
    ]


def _llm_response(overrides: dict | None = None) -> dict:
    base = {
        "macro_view": "Easing cycle underway with growth holding up.",
        "macro_drivers": ["Fed rate cuts reducing discount rates", "Yield curve steepening"],
        "macro_risks": [
            {"name": "Inflation re-acceleration", "impact": "high", "signal": "CPI prints above 3%"},
        ],
        "macro_signals": ["Watch DGS10 for further steepening", "VIX for risk sentiment"],
        "rate_environment": "easing",
        "growth_environment": "expanding",
        "missing_fields": [],
    }
    if overrides:
        base.update(overrides)
    return base


def _mock_llm(response: dict | None = None, raises: Exception | None = None):
    llm = MagicMock()
    if raises:
        llm.call_with_retry = AsyncMock(side_effect=raises)
    else:
        llm.call_with_retry = AsyncMock(return_value=json.dumps(response or _llm_response()))
    return llm


def _state(evidence=None, intent=None):
    return {
        "query": "Analyse NVDA",
        "intent": intent or ResearchIntent(ticker="NVDA", subjects=["NVDA"], scope="company"),
        "evidence": evidence if evidence is not None else _evidence(),
        "agent_statuses": [],
    }


# ── output shape ───────────────────────────────────────────────────────────

def test_macro_result_shape_and_core_fields():
    result = _run(macro_analysis_node(_state(), llm=_mock_llm()))
    ma = result["macro_analysis"]
    assert isinstance(ma, MacroAnalysis)
    assert isinstance(ma.macro_view, str) and ma.macro_view
    assert ma.rate_environment in ("tightening", "easing", "stable")
    assert ma.growth_environment in ("expanding", "contracting", "stable")
    assert len(ma.macro_drivers) >= 1
    for risk in ma.macro_risks:
        assert risk.impact in ("high", "medium", "low")


# ── missing fields → agent_questions ──────────────────────────────────────

def test_agent_questions_empty_when_no_missing():
    result = _run(macro_analysis_node(_state(), llm=_mock_llm()))
    assert result["agent_questions"] == []


def test_agent_questions_populated_from_missing_fields():
    resp = _llm_response({"missing_fields": ["GDPC1", "credit_spread"]})
    result = _run(macro_analysis_node(_state(), llm=_mock_llm(resp)))
    qs = result["agent_questions"]
    assert len(qs) == 2
    assert all("macro_analysis needs" in q for q in qs)


# ── evidence filtering ─────────────────────────────────────────────────────

def test_macro_only_evidence_still_produces_result():
    ev = _evidence(["macro_api", "macro_api"])
    result = _run(macro_analysis_node(_state(evidence=ev), llm=_mock_llm()))
    assert isinstance(result["macro_analysis"], MacroAnalysis)


def test_no_macro_evidence_still_runs_with_supplemental():
    ev = _evidence(["financial_api", "web"])
    result = _run(macro_analysis_node(_state(evidence=ev), llm=_mock_llm()))
    assert isinstance(result["macro_analysis"], MacroAnalysis)


# ── failure handling ───────────────────────────────────────────────────────

def test_raises_on_llm_failure():
    with pytest.raises(RuntimeError, match="macro_analysis"):
        _run(macro_analysis_node(_state(), llm=_mock_llm(raises=RuntimeError("exhausted"))))


def test_raises_on_bad_json():
    llm = MagicMock()
    llm.call_with_retry = AsyncMock(return_value="not json")
    with pytest.raises(RuntimeError, match="macro_analysis"):
        _run(macro_analysis_node(_state(), llm=llm))
