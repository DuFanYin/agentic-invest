"""Unit tests for llm_judge router function."""

from src.server.agents.llm_judge import llm_judge_router_fn
from src.server.services.policy import PolicyDecision


def _pd(**overrides) -> PolicyDecision:
    base = dict(
        action="continue",
        targets=[],
        retry_question="",
        reason_code="default_continue",
        rationale="test",
    )
    base.update(overrides)
    return PolicyDecision(**base)


def _state(**overrides):
    base = dict(
        policy_decision=_pd(),
    )
    base.update(overrides)
    return base


# ── llm_judge_router_fn ───────────────────────────────────────────────────


def test_router_fn_routes_retry_to_research():
    state = _state(policy_decision=_pd(action="retry_full_research"))
    assert llm_judge_router_fn(state) == "research"


def test_router_fn_routes_capability_retry_to_research():
    state = _state(policy_decision=_pd(action="retry_capability_only"))
    assert llm_judge_router_fn(state) == "research"


def test_router_fn_routes_continue_to_scenario_scoring():
    state = _state(policy_decision=_pd(action="continue"))
    assert llm_judge_router_fn(state) == "scenario_scoring"


def test_router_fn_routes_halt_to_scenario_scoring():
    state = _state(policy_decision=_pd(action="halt_with_degraded_output"))
    assert llm_judge_router_fn(state) == "scenario_scoring"


def test_router_fn_no_decision_routes_to_scenario_scoring():
    state = _state()
    state.pop("policy_decision")
    assert llm_judge_router_fn(state) == "scenario_scoring"
