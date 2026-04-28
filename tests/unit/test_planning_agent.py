"""Unit tests for planning_agent — plan() and make_planning_node()."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from src.server.agents.planning_agent import make_planning_node, plan
from src.server.models.intent import ResearchIntent
from src.server.services.llm_provider import LLMClient


def _run(coro):
    return asyncio.run(coro)


def _llm_response(overrides: dict | None = None) -> dict:
    base = {
        "intent": "investment_research",
        "subjects": ["NVDA"],
        "scope": "company",
        "ticker": "NVDA",
        "time_horizon": "3-5 years",
        "risk_level": "medium",
        "required_outputs": ["valuation", "risks", "scenarios"],
        "research_focus": [
            "Is NVDA valuation justified by AI capex cycle?",
            "Assess margin sustainability under rising competition",
        ],
        "must_have_metrics": [
            "pe_ratio",
            "revenue_growth_yoy",
            "gross_margin_pct",
            "free_cash_flow",
        ],
        "plan_notes": [
            "Benchmark against semiconductor peers",
            "Model sensitivity to hyperscaler capex slowdown",
        ],
    }
    if overrides:
        base.update(overrides)
    return base


def _mock_llm(response: dict | None = None, raises: Exception | None = None):
    llm = MagicMock(spec=LLMClient)
    if raises:
        llm.complete = AsyncMock(side_effect=raises)
    else:
        llm.complete = AsyncMock(return_value=json.dumps(response or _llm_response()))
    return llm


# ── plan() output shape ────────────────────────────────────────────────────


def test_plan_returns_structured_result():
    result = _run(plan("Analyse NVDA for long-term", _mock_llm()))
    assert isinstance(result.intent, ResearchIntent)
    assert result.intent.ticker == "NVDA"
    assert result.intent.time_horizon == "3-5 years"
    assert len(result.research_focus) >= 1
    assert len(result.must_have_metrics) >= 1
    assert len(result.plan_notes) >= 1
    assert all(isinstance(f, str) for f in result.research_focus)
    assert all(isinstance(m, str) for m in result.must_have_metrics)
    assert all(isinstance(n, str) for n in result.plan_notes)


# ── fallback on LLM failure ────────────────────────────────────────────────


def test_plan_fallback_on_llm_error():
    result = _run(plan("Analyse NVDA", _mock_llm(raises=RuntimeError("no key"))))
    assert isinstance(result.intent, ResearchIntent)
    assert result.intent.intent == "investment_research"
    assert len(result.research_focus) >= 1
    assert len(result.must_have_metrics) >= 1
    assert len(result.plan_notes) >= 1


# ── make_planning_node() ────────────────────────────────────────────────────


def test_node_returns_state_fields():
    from src.server.models.analysis import PlanContext

    node = make_planning_node(_mock_llm())
    state = {"query": "Analyse NVDA", "agent_statuses": []}
    result = _run(node(state))
    assert isinstance(result["intent"], ResearchIntent)
    assert isinstance(result["plan_context"], PlanContext)
    assert isinstance(result["plan_context"].research_focus, list)
    assert isinstance(result["plan_context"].must_have_metrics, list)
    assert isinstance(result["plan_context"].plan_notes, list)
    assert result["research_iteration"] == 0
    assert result["retry_questions"] == []
