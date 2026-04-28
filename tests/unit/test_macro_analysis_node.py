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


# ── failure handling ───────────────────────────────────────────────────────

def test_raises_on_llm_failure():
    with pytest.raises(RuntimeError, match="macro_analysis"):
        _run(macro_analysis_node(_state(), llm=_mock_llm(raises=RuntimeError("exhausted"))))
