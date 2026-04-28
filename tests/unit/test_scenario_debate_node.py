"""Unit tests for scenario_debate_node — concurrent advocates + arbitrator."""

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


def _advocate_response(scenario_name: str, claim: float = 0.50) -> dict:
    return {
        "scenario_name": scenario_name,
        "advocacy_thesis": f"Evidence strongly supports {scenario_name}.",
        "probability_claim": claim,
        "supporting_arguments": ["Revenue growth validates this scenario."],
        "evidence_refs": ["ev_001"],
        "contested_scenarios": [],
    }


def _arbitrator_response(overrides: dict | None = None) -> dict:
    base = {
        "debate_summary": "AI Supercycle advocacy was strongest; Capex Retreat weakened.",
        "probability_adjustments": [
            {
                "scenario_name": "AI Supercycle",
                "before": 0.45,
                "after": 0.50,
                "delta": 0.05,
                "reason": "Strongest evidence backing.",
                "evidence_refs": ["ev_001"],
            },
            {
                "scenario_name": "Capex Retreat",
                "before": 0.20,
                "after": 0.15,
                "delta": -0.05,
                "reason": "No near-term spending pullback signals.",
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


def _mock_llm(
    arbitrator: dict | None = None,
    advocate_fails: bool = False,
    arbitrator_fails: bool = False,
    all_advocates_fail: bool = False,
):
    """
    Advocates are calls 0..N-1 (one per scenario), arbitrator is the final call.
    call_with_retry is called N+1 times total for N scenarios.
    """
    scenarios = _scenarios()
    n = len(scenarios)
    llm = MagicMock()
    call_count = [0]

    async def side_effect(*args, **kwargs):
        idx = call_count[0]
        call_count[0] += 1

        is_advocate = idx < n
        if is_advocate:
            if all_advocates_fail or advocate_fails:
                raise RuntimeError("advocate failed")
            scenario_name = scenarios[idx].name
            return json.dumps(_advocate_response(scenario_name))
        else:
            if arbitrator_fails:
                raise RuntimeError("arbitrator failed")
            return json.dumps(arbitrator or _arbitrator_response())

    llm.call_with_retry = AsyncMock(side_effect=side_effect)
    return llm


def _state(scenarios=None):
    return {
        "query": "Analyse NVDA",
        "scenarios": scenarios if scenarios is not None else _scenarios(),
        "evidence": [],
        "agent_statuses": [],
    }


# ── output shape ───────────────────────────────────────────────────────────

def test_result_shape_and_core_fields():
    result = _run(scenario_debate_node(_state(), llm=_mock_llm()))
    debate = result["scenario_debate"]
    assert isinstance(debate, ScenarioDebate)
    assert isinstance(debate.debate_summary, str) and debate.debate_summary
    assert len(debate.calibrated_scenarios) == 3
    assert debate.confidence in ("high", "medium", "low")


def test_advocacy_summaries_populated():
    result = _run(scenario_debate_node(_state(), llm=_mock_llm()))
    summaries = result["scenario_debate"].advocacy_summaries
    assert len(summaries) == 3
    names = {s["scenario_name"] for s in summaries}
    assert "AI Supercycle" in names
    assert "Capex Retreat" in names


def test_n_plus_one_llm_calls_for_n_scenarios():
    """N concurrent advocate calls + 1 arbitrator call."""
    llm = _mock_llm()
    _run(scenario_debate_node(_state(), llm=llm))
    assert llm.call_with_retry.call_count == len(_scenarios()) + 1


# ── probability constraints ────────────────────────────────────────────────

def test_calibrated_probabilities_sum_to_one():
    result = _run(scenario_debate_node(_state(), llm=_mock_llm()))
    total = sum(s.get("probability", 0) for s in result["scenario_debate"].calibrated_scenarios)
    assert abs(total - 1.0) < 0.01


def test_probability_cap_enforced():
    arb = _arbitrator_response({
        "probability_adjustments": [{
            "scenario_name": "AI Supercycle",
            "before": 0.45,
            "after": 0.75,   # +0.30 — exceeds ±0.15 cap
            "delta": 0.30,
            "reason": "Extreme claim.",
            "evidence_refs": ["ev_001"],
        }],
    })
    result = _run(scenario_debate_node(_state(), llm=_mock_llm(arbitrator=arb)))
    adj = result["scenario_debate"].probability_adjustments
    if adj:
        assert all(abs(a.delta) <= 0.15 + 1e-6 for a in adj)


def test_normalisation_fixes_bad_sum():
    arb = _arbitrator_response({
        "calibrated_scenarios": [
            {"name": "AI Supercycle", "probability": 0.60, "tags": ["bullish-2"]},
            {"name": "Soft Landing",  "probability": 0.60, "tags": ["neutral"]},
            {"name": "Capex Retreat", "probability": 0.30, "tags": ["bearish-2"]},
        ]
    })
    result = _run(scenario_debate_node(_state(), llm=_mock_llm(arbitrator=arb)))
    total = sum(s.get("probability", 0) for s in result["scenario_debate"].calibrated_scenarios)
    assert abs(total - 1.0) < 0.01


def test_missing_scenario_coverage_falls_back_to_baseline():
    arb = _arbitrator_response({
        "calibrated_scenarios": [
            {"name": "AI Supercycle", "probability": 0.65, "tags": ["bullish-2"]},
            {"name": "Soft Landing",  "probability": 0.35, "tags": ["neutral"]},
            # Capex Retreat missing — must trigger fallback
        ]
    })
    result = _run(scenario_debate_node(_state(), llm=_mock_llm(arbitrator=arb)))
    debate = result["scenario_debate"]
    assert "fallback_to_baseline" in debate.debate_flags
    assert len(debate.calibrated_scenarios) == 3


# ── partial advocate failure ───────────────────────────────────────────────

def test_partial_advocate_failure_still_runs_arbitrator():
    """If some but not all advocates fail, arbitration still runs with partial input."""
    scenarios = _scenarios()
    n = len(scenarios)
    llm = MagicMock()
    call_count = [0]

    async def side_effect(*args, **kwargs):
        idx = call_count[0]
        call_count[0] += 1
        if idx < n:
            if idx == 1:  # only Soft Landing advocate fails
                raise RuntimeError("advocate failed")
            return json.dumps(_advocate_response(scenarios[idx].name))
        return json.dumps(_arbitrator_response())

    llm.call_with_retry = AsyncMock(side_effect=side_effect)
    result = _run(scenario_debate_node(_state(), llm=llm))
    debate = result["scenario_debate"]
    assert isinstance(debate, ScenarioDebate)
    assert any("partial_advocacy" in f for f in debate.debate_flags)


# ── fallback paths ─────────────────────────────────────────────────────────

def test_fallback_when_all_advocates_fail():
    result = _run(scenario_debate_node(_state(), llm=_mock_llm(all_advocates_fail=True)))
    debate = result["scenario_debate"]
    assert isinstance(debate, ScenarioDebate)
    assert "fallback_to_baseline" in debate.debate_flags


def test_fallback_when_arbitrator_fails():
    result = _run(scenario_debate_node(_state(), llm=_mock_llm(arbitrator_fails=True)))
    debate = result["scenario_debate"]
    assert "fallback_to_baseline" in debate.debate_flags


def test_fallback_when_arbitrator_returns_bad_json():
    llm = MagicMock()
    scenarios = _scenarios()
    n = len(scenarios)
    call_count = [0]

    async def side_effect(*args, **kwargs):
        idx = call_count[0]
        call_count[0] += 1
        if idx < n:
            return json.dumps(_advocate_response(scenarios[idx].name))
        return "not json at all"

    llm.call_with_retry = AsyncMock(side_effect=side_effect)
    result = _run(scenario_debate_node(_state(), llm=llm))
    assert "fallback_to_baseline" in result["scenario_debate"].debate_flags


def test_fallback_returns_all_baseline_scenario_names():
    result = _run(scenario_debate_node(_state(), llm=_mock_llm(all_advocates_fail=True)))
    names = {s.get("name") for s in result["scenario_debate"].calibrated_scenarios}
    assert "AI Supercycle" in names
    assert "Capex Retreat" in names


def test_empty_scenarios_produces_fallback():
    result = _run(scenario_debate_node(_state(scenarios=[]), llm=_mock_llm()))
    debate = result["scenario_debate"]
    assert isinstance(debate, ScenarioDebate)
    assert "fallback_to_baseline" in debate.debate_flags


# ── no adjustments edge case ───────────────────────────────────────────────

def test_no_adjustments_still_valid():
    arb = _arbitrator_response({"probability_adjustments": []})
    result = _run(scenario_debate_node(_state(), llm=_mock_llm(arbitrator=arb)))
    assert result["scenario_debate"].probability_adjustments == []
    assert result["scenario_debate"].calibrated_scenarios
