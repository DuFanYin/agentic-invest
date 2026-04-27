"""Unit tests for planning_agent — plan() and make_planning_node()."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.server.agents.planning_agent import plan, make_planning_node
from src.server.models.intent import ResearchIntent
from src.server.services.openrouter import OpenRouterClient


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
        "must_have_metrics": ["pe_ratio", "revenue_growth_yoy", "gross_margin_pct", "free_cash_flow"],
        "plan_notes": [
            "Benchmark against semiconductor peers",
            "Model sensitivity to hyperscaler capex slowdown",
        ],
    }
    if overrides:
        base.update(overrides)
    return base


def _mock_llm(response: dict | None = None, raises: Exception | None = None):
    llm = MagicMock(spec=OpenRouterClient)
    if raises:
        llm.complete = AsyncMock(side_effect=raises)
    else:
        llm.complete = AsyncMock(return_value=json.dumps(response or _llm_response()))
    return llm


# ── plan() output shape ────────────────────────────────────────────────────

def test_plan_returns_typed_intent():
    result = _run(plan("Analyse NVDA for long-term", _mock_llm()))
    assert isinstance(result.intent, ResearchIntent)


def test_plan_populates_research_focus():
    result = _run(plan("Analyse NVDA for long-term", _mock_llm()))
    assert len(result.research_focus) >= 1
    assert all(isinstance(f, str) for f in result.research_focus)


def test_plan_populates_must_have_metrics():
    result = _run(plan("Analyse NVDA for long-term", _mock_llm()))
    assert len(result.must_have_metrics) >= 1
    assert all(isinstance(m, str) for m in result.must_have_metrics)


def test_plan_populates_plan_notes():
    result = _run(plan("Analyse NVDA for long-term", _mock_llm()))
    assert len(result.plan_notes) >= 1
    assert all(isinstance(n, str) for n in result.plan_notes)


def test_plan_ticker_parsed():
    result = _run(plan("Analyse NVDA", _mock_llm()))
    assert result.intent.ticker == "NVDA"


def test_plan_horizon_parsed():
    result = _run(plan("Analyse NVDA", _mock_llm()))
    assert result.intent.time_horizon == "3-5 years"


# ── fallback on LLM failure ────────────────────────────────────────────────

def test_plan_fallback_on_llm_error():
    result = _run(plan("Analyse NVDA", _mock_llm(raises=RuntimeError("no key"))))
    assert isinstance(result.intent, ResearchIntent)
    assert result.intent.intent == "investment_research"
    assert len(result.research_focus) >= 1
    assert len(result.must_have_metrics) >= 1
    assert len(result.plan_notes) >= 1


def test_plan_fallback_on_bad_json():
    llm = MagicMock(spec=OpenRouterClient)
    llm.complete = AsyncMock(return_value="not json at all")
    result = _run(plan("Analyse NVDA", llm))
    assert isinstance(result.intent, ResearchIntent)
    assert result.research_focus  # non-empty fallback


# ── fallback when planning fields empty ───────────────────────────────────

def test_plan_derives_fallback_when_focus_empty():
    resp = _llm_response({"research_focus": [], "must_have_metrics": [], "plan_notes": []})
    result = _run(plan("Analyse NVDA", _mock_llm(resp)))
    assert len(result.research_focus) >= 1
    assert len(result.must_have_metrics) >= 1
    assert len(result.plan_notes) >= 1


# ── make_planning_node() ────────────────────────────────────────────────────

def test_node_returns_state_fields():
    node = make_planning_node(_mock_llm())
    state = {"query": "Analyse NVDA", "agent_statuses": []}
    result = _run(node(state))
    assert isinstance(result["intent"], ResearchIntent)
    assert isinstance(result["research_focus"], list)
    assert isinstance(result["must_have_metrics"], list)
    assert isinstance(result["plan_notes"], list)
    assert result["research_iteration"] == 0
    assert result["retry_questions"] == []


def test_node_resets_iteration():
    node = make_planning_node(_mock_llm())
    state = {"query": "Analyse NVDA", "agent_statuses": [], "research_iteration": 5}
    result = _run(node(state))
    assert result["research_iteration"] == 0
