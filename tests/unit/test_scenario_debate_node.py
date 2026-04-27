"""Unit tests for scenario_debate_node."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.server.agents.scenario_debate import scenario_debate_node
from src.server.models.analysis import ScenarioDebate
from src.server.models.scenario import Scenario


def _run(coro):
    return asyncio.run(coro)


def _scenarios() -> list[Scenario]:
    return [
        Scenario(id="sc_001", name="AI Supercycle", description="AI demand remains strong.", probability=0.45, tags=["bullish-2"]),
        Scenario(id="sc_002", name="Soft Landing", description="Moderate growth continues.", probability=0.35, tags=["neutral"]),
        Scenario(id="sc_003", name="Capex Retreat", description="Hyperscalers cut AI spending.", probability=0.20, tags=["bearish-2"]),
    ]


def _llm_response(overrides: dict | None = None) -> dict:
    base = {
        "debate_summary": "Bull case is well-supported by evidence; bear risk is real but less likely.",
        "probability_adjustments": [
            {
                "scenario_name": "AI Supercycle",
                "before": 0.45,
                "after": 0.50,
                "delta": 0.05,
                "reason": "Strong hyperscaler capex commitments cited in ev_001.",
                "evidence_refs": ["ev_001"],
            },
            {
                "scenario_name": "Capex Retreat",
                "before": 0.20,
                "after": 0.15,
                "delta": -0.05,
                "reason": "No leading indicators of a near-term spending pullback.",
                "evidence_refs": ["ev_002"],
            },
        ],
        "calibrated_scenarios": [
            {"name": "AI Supercycle", "probability": 0.50, "tags": ["bullish-2"]},
            {"name": "Soft Landing",  "probability": 0.35, "tags": ["neutral"]},
            {"name": "Capex Retreat", "probability": 0.15, "tags": ["bearish-2"]},
        ],
        "confidence": "high",
        "debate_flags": [],
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


def _state(scenarios=None):
    return {
        "query": "Analyse NVDA",
        "scenarios": scenarios if scenarios is not None else _scenarios(),
        "evidence": [],
        "agent_statuses": [],
    }


# ── output shape ───────────────────────────────────────────────────────────

def test_result_is_typed_model():
    result = _run(scenario_debate_node(_state(), llm=_mock_llm()))
    assert isinstance(result["scenario_debate"], ScenarioDebate)


def test_debate_summary_present():
    result = _run(scenario_debate_node(_state(), llm=_mock_llm()))
    assert isinstance(result["scenario_debate"].debate_summary, str)
    assert len(result["scenario_debate"].debate_summary) > 0


def test_calibrated_scenarios_present():
    result = _run(scenario_debate_node(_state(), llm=_mock_llm()))
    assert len(result["scenario_debate"].calibrated_scenarios) == 3


def test_confidence_valid():
    result = _run(scenario_debate_node(_state(), llm=_mock_llm()))
    assert result["scenario_debate"].confidence in ("high", "medium", "low")


# ── probability constraints ────────────────────────────────────────────────

def test_calibrated_probabilities_sum_to_one():
    result = _run(scenario_debate_node(_state(), llm=_mock_llm()))
    total = sum(s.get("probability", 0) for s in result["scenario_debate"].calibrated_scenarios)
    assert abs(total - 1.0) < 0.01


def test_probability_cap_enforced():
    # LLM tries to move a scenario by 0.30 — should be clamped to 0.15
    resp = _llm_response({
        "probability_adjustments": [{
            "scenario_name": "AI Supercycle",
            "before": 0.45,
            "after": 0.75,   # +0.30 — too large
            "delta": 0.30,
            "reason": "Extreme bull case.",
            "evidence_refs": ["ev_001"],
        }],
    })
    result = _run(scenario_debate_node(_state(), llm=_mock_llm(resp)))
    adj = result["scenario_debate"].probability_adjustments
    if adj:
        assert all(abs(a.delta) <= 0.15 + 1e-6 for a in adj)


def test_normalisation_fixes_bad_sum():
    # LLM returns probabilities that sum to 1.5
    resp = _llm_response({
        "calibrated_scenarios": [
            {"name": "AI Supercycle", "probability": 0.60, "tags": ["bullish-2"]},
            {"name": "Soft Landing",  "probability": 0.60, "tags": ["neutral"]},
            {"name": "Capex Retreat", "probability": 0.30, "tags": ["bearish-2"]},
        ]
    })
    result = _run(scenario_debate_node(_state(), llm=_mock_llm(resp)))
    total = sum(s.get("probability", 0) for s in result["scenario_debate"].calibrated_scenarios)
    assert abs(total - 1.0) < 0.01


def test_missing_scenario_coverage_falls_back_to_baseline():
    resp = _llm_response({
        "calibrated_scenarios": [
            {"name": "AI Supercycle", "probability": 0.60, "tags": ["bullish-2"]},
            {"name": "Soft Landing", "probability": 0.40, "tags": ["neutral"]},
        ]
    })
    result = _run(scenario_debate_node(_state(), llm=_mock_llm(resp)))
    debate = result["scenario_debate"]
    assert "fallback_to_baseline" in debate.debate_flags
    assert len(debate.calibrated_scenarios) == 3


# ── fallback paths ─────────────────────────────────────────────────────────

def test_fallback_on_llm_failure():
    result = _run(scenario_debate_node(_state(), llm=_mock_llm(raises=RuntimeError("all exhausted"))))
    debate = result["scenario_debate"]
    assert isinstance(debate, ScenarioDebate)
    assert "fallback_to_baseline" in debate.debate_flags


def test_fallback_on_bad_json():
    llm = MagicMock()
    llm.call_with_retry = AsyncMock(return_value="not json")
    result = _run(scenario_debate_node(_state(), llm=llm))
    assert "fallback_to_baseline" in result["scenario_debate"].debate_flags


def test_fallback_returns_baseline_probabilities():
    result = _run(scenario_debate_node(_state(), llm=_mock_llm(raises=RuntimeError("err"))))
    debate = result["scenario_debate"]
    names = {s.get("name") for s in debate.calibrated_scenarios}
    assert "AI Supercycle" in names
    assert "Capex Retreat" in names


def test_empty_scenarios_produces_fallback():
    result = _run(scenario_debate_node(_state(scenarios=[]), llm=_mock_llm()))
    debate = result["scenario_debate"]
    assert isinstance(debate, ScenarioDebate)
    assert "fallback_to_baseline" in debate.debate_flags


# ── no-op when no adjustments ──────────────────────────────────────────────

def test_no_adjustments_still_valid():
    resp = _llm_response({"probability_adjustments": []})
    result = _run(scenario_debate_node(_state(), llm=_mock_llm(resp)))
    assert result["scenario_debate"].probability_adjustments == []
    assert result["scenario_debate"].calibrated_scenarios
