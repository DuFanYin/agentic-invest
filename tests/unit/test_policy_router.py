"""Unit tests for policy_router_node and policy_router_fn."""

import asyncio

from src.server.agents.policy_router import policy_router_fn, policy_router_node
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
        evidence=[],
        normalized_data=None,
        fundamental_analysis=None,
        macro_analysis=None,
        market_sentiment=None,
        research_iteration=1,
        policy_decision=_pd(),
        agent_statuses=[],
    )
    base.update(overrides)
    return base


# ── policy_router_node ────────────────────────────────────────────────────

def test_router_node_continue_writes_empty_retry():
    result = asyncio.run(policy_router_node(_state()))
    assert result["retry_questions"] == []
    assert result["retry_reason"] == "default"
    assert result["retry_scope"] is None
    assert result["policy_decision"].action == "continue"


def test_router_node_full_retry_writes_questions():
    result = asyncio.run(policy_router_node(_state(
        policy_decision=_pd(
            action="retry_full_research",
            retry_question="find EPS data",
            reason_code="analysis_robustness",
        ),
        research_iteration=1,
    )))
    assert result["policy_decision"].action == "retry_full_research"
    assert result["retry_questions"] == ["find EPS data"]
    assert result["retry_scope"] is None


def test_router_node_scoped_retry_sets_scope():
    result = asyncio.run(policy_router_node(_state(
        policy_decision=_pd(
            action="retry_capability_only",
            targets=["cap.fetch_web"],
            retry_question="resolve conflict",
            reason_code="evidence_conflict",
        ),
        research_iteration=1,
    )))
    assert result["policy_decision"].action == "retry_capability_only"
    assert result["retry_scope"] == ["cap.fetch_web"]
    assert result["retry_questions"] == ["resolve conflict"]


def test_router_node_missing_policy_decision_falls_through():
    """No policy_decision in state → safe fallthrough, no retry."""
    state = _state()
    state.pop("policy_decision")
    result = asyncio.run(policy_router_node(state))
    assert result["retry_questions"] == []
    assert result["retry_reason"] == "none"
    assert result["retry_scope"] is None


def test_router_node_iteration_limit_overrides_to_continue():
    """When iteration limit fires, action becomes 'continue' even if judge hinted retry."""
    result = asyncio.run(policy_router_node(_state(
        policy_decision=_pd(
            action="retry_full_research",
            retry_question="structural gap",
            reason_code="structural",
        ),
        research_iteration=2,  # at max (MAX_RESEARCH_ITERATIONS=2)
    )))
    # policy engine's rule_iteration_limit fires and forces continue
    assert result["policy_decision"].action == "continue"
    assert result["retry_questions"] == []


# ── policy_router_fn ──────────────────────────────────────────────────────

def test_router_fn_routes_retry_to_research():
    state = _state(policy_decision=_pd(action="retry_full_research"))
    assert policy_router_fn(state) == "research"


def test_router_fn_routes_capability_retry_to_research():
    state = _state(policy_decision=_pd(action="retry_capability_only"))
    assert policy_router_fn(state) == "research"


def test_router_fn_routes_continue_to_scenario_scoring():
    state = _state(policy_decision=_pd(action="continue"))
    assert policy_router_fn(state) == "scenario_scoring"


def test_router_fn_routes_halt_to_scenario_scoring():
    state = _state(policy_decision=_pd(action="halt_with_degraded_output"))
    assert policy_router_fn(state) == "scenario_scoring"


def test_router_fn_no_decision_routes_to_scenario_scoring():
    state = _state()
    state.pop("policy_decision")
    assert policy_router_fn(state) == "scenario_scoring"
