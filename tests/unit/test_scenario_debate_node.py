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
        Scenario(name="AI Supercycle", description="Strong AI demand.", probability=0.45, tags=["bullish-2"]),
        Scenario(name="Soft Landing", description="Moderate growth.", probability=0.35, tags=["neutral"]),
        Scenario(name="Capex Retreat", description="Spending cuts.", probability=0.20, tags=["bearish-2"]),
    ]


def _mock_llm(advocate_fails: bool = False, arbitrator_fails: bool = False):
    scenarios = _scenarios()
    n = len(scenarios)
    llm = MagicMock()
    call_count = [0]

    async def side_effect(*args, **kwargs):
        idx = call_count[0]
        call_count[0] += 1
        if idx < n:
            if advocate_fails:
                raise RuntimeError("advocate failed")
            return json.dumps(
                {
                    "scenario_name": scenarios[idx].name,
                    "advocacy_thesis": "Strong case.",
                    "probability_claim": 0.5,
                    "supporting_arguments": ["Evidence supports this."],
                    "evidence_refs": ["ev_001"],
                    "contested_scenarios": [],
                }
            )
        if arbitrator_fails:
            raise RuntimeError("arbitrator failed")
        return json.dumps(
            {
                "debate_summary": "AI Supercycle strongest.",
                "probability_adjustments": [],
                "calibrated_scenarios": [
                    {"name": s.name, "probability": s.probability, "tags": list(s.tags)} for s in scenarios
                ],
                "confidence": "high",
                "debate_flags": [],
            }
        )

    llm.call_with_retry = AsyncMock(side_effect=side_effect)
    return llm


def _state():
    return {"query": "Analyse NVDA", "scenarios": _scenarios(), "evidence": [], "agent_statuses": []}


def test_happy_path_shape_and_probabilities():
    result = _run(scenario_debate_node(_state(), llm=_mock_llm()))
    debate = result["scenario_debate"]
    assert isinstance(debate, ScenarioDebate)
    assert not debate.degraded
    assert len(debate.calibrated_scenarios) == 3
    total = sum(s.probability for s in debate.calibrated_scenarios)
    assert abs(total - 1.0) < 0.01


@pytest.mark.parametrize("advocate_fails,arbitrator_fails", [(True, False), (False, True)])
def test_degrades_on_failure(advocate_fails, arbitrator_fails):
    result = _run(
        scenario_debate_node(_state(), llm=_mock_llm(advocate_fails=advocate_fails, arbitrator_fails=arbitrator_fails))
    )
    debate = result["scenario_debate"]
    assert debate.degraded is True
    assert "debate_degraded" in debate.debate_flags
