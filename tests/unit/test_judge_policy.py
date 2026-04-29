"""Unit tests for policy engine rules and llm_judge router function."""

from __future__ import annotations

import pytest
from src.server.agents.llm_judge import llm_judge_router_fn
from src.server.services.policy import PolicyDecision, PolicyInput, evaluate_policy


# ── helpers ────────────────────────────────────────────────────────────────


def _inp(**overrides) -> PolicyInput:
    base = dict(
        research_iteration=1,
        fa_degraded=False,
        macro_degraded=False,
        ms_degraded=False,
        judge_reason="none",
        judge_retry_question="",
        max_iterations=2,
    )
    base.update(overrides)
    return PolicyInput(**base)


def _pd(**overrides) -> PolicyDecision:
    base = dict(action="continue", targets=[], retry_question="", reason_code="default", rationale="test")
    base.update(overrides)
    return PolicyDecision(**base)


def _state(**overrides):
    return {"policy_decision": _pd(), **overrides}


# ── policy rules ───────────────────────────────────────────────────────────


def test_iteration_limit_fires_at_max():
    dec = evaluate_policy(
        _inp(research_iteration=2, max_iterations=2, judge_reason="structural", judge_retry_question="fix ticker")
    )
    assert dec.action == "continue"
    assert dec.reason_code == "iteration_limit"


def test_structural_gap_triggers_full_retry():
    dec = evaluate_policy(_inp(judge_reason="structural", judge_retry_question="need ticker"))
    assert dec.action == "retry_full_research"
    assert dec.reason_code == "structural"
    assert dec.retry_question == "need ticker"


def test_all_degraded_halts_pipeline():
    dec = evaluate_policy(_inp(fa_degraded=True, macro_degraded=True, ms_degraded=True))
    assert dec.action == "halt_with_degraded_output"
    assert dec.reason_code == "all_degraded"


def test_partial_degraded_does_not_halt():
    dec = evaluate_policy(_inp(fa_degraded=True))
    assert dec.action == "continue"


def test_evidence_conflict_triggers_web_retry():
    dec = evaluate_policy(_inp(judge_reason="evidence_conflict", judge_retry_question="resolve EPS conflict"))
    assert dec.action == "retry_capability_only"
    assert "cap.fetch_web" in dec.targets
    assert dec.retry_question == "resolve EPS conflict"


def test_analysis_robustness_triggers_full_retry():
    dec = evaluate_policy(_inp(judge_reason="analysis_robustness", judge_retry_question="get margin data"))
    assert dec.action == "retry_full_research"
    assert dec.reason_code == "analysis_robustness"


def test_default_continue_when_no_issues():
    dec = evaluate_policy(_inp())
    assert dec.action == "continue"
    assert dec.reason_code == "default"


# ── priority ordering ─────────────────────────────────────────────────────


def test_iteration_limit_beats_structural():
    dec = evaluate_policy(
        _inp(research_iteration=2, max_iterations=2, judge_reason="structural", judge_retry_question="need ticker")
    )
    assert dec.reason_code == "iteration_limit"


def test_all_degraded_beats_evidence_conflict():
    dec = evaluate_policy(
        _inp(
            fa_degraded=True,
            macro_degraded=True,
            ms_degraded=True,
            judge_reason="evidence_conflict",
            judge_retry_question="resolve",
        )
    )
    assert dec.action == "halt_with_degraded_output"


# ── router function ────────────────────────────────────────────────────────


@pytest.mark.parametrize("action", ["retry_full_research", "retry_capability_only"])
def test_router_routes_retry_to_research(action):
    assert llm_judge_router_fn(_state(policy_decision=_pd(action=action))) == "research"


def test_router_routes_continue_to_scenario_scoring():
    assert llm_judge_router_fn(_state(policy_decision=_pd(action="continue"))) == "scenario_scoring"


def test_router_routes_halt_to_report_finalize():
    assert llm_judge_router_fn(_state(policy_decision=_pd(action="halt_with_degraded_output"))) == "report_finalize"


def test_router_missing_decision_defaults_to_scenario_scoring():
    state = {}
    assert llm_judge_router_fn(state) == "scenario_scoring"
