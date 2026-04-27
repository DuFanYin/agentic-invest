from src.server.agents.retry_gate import (
    MAX_RESEARCH_ITERATIONS,
    retry_gate_node,
    retry_router,
)
from src.server.models.analysis import NormalizedData
from src.server.models.intent import ResearchIntent


def _state(**overrides):
    base = {
        "intent": ResearchIntent(ticker="AAPL", subjects=["Apple"], scope="company", time_horizon="3 years"),
        "agent_questions": [],
        "retry_questions": [],
        "research_iteration": 0,
        "normalized_data": None,
        "agent_statuses": [],
    }
    base.update(overrides)
    return base


def test_router_retries_when_questions_present():
    assert retry_router(_state(retry_questions=["q1"])) == "research"


def test_router_proceeds_when_no_questions():
    assert retry_router(_state(retry_questions=[])) == "scenario_scoring"


def test_gate_clears_after_max_iterations():
    result = retry_gate_node(
        _state(
            intent=ResearchIntent(ticker=None, subjects=["Apple"], scope="company", time_horizon=None),
            research_iteration=MAX_RESEARCH_ITERATIONS,
        )
    )
    assert result["retry_questions"] == []
    assert result["agent_questions"] != []


def test_gate_collects_structural_and_agent_questions():
    result = retry_gate_node(
        _state(
            intent=ResearchIntent(ticker=None, subjects=["Apple"], scope="company", time_horizon=None),
            agent_questions=["Need margins by segment"],
            research_iteration=0,
        )
    )
    joined = " | ".join(result["retry_questions"])
    assert "ticker" in joined.lower()
    assert "horizon" in joined.lower()
    assert "margins by segment" in joined


def test_gate_skips_ticker_horizon_checks_for_macro_scope():
    result = retry_gate_node(
        _state(
            intent=ResearchIntent(
                ticker=None,
                subjects=["US inflation outlook"],
                scope="macro",
                time_horizon=None,
            ),
            agent_questions=[],
            research_iteration=0,
        )
    )
    assert result["retry_questions"] == []


def test_gate_handles_typed_conflicts_without_crashing():
    normalized = NormalizedData.model_validate({
        "query": "Analyse AAPL",
        "intent": {},
        "metrics": {},
        "missing_fields": [],
        "conflicts": [
            {"topic": "valuation", "type": "reliability_divergence", "evidence_ids": ["ev_001"], "note": "x"},
            {"topic": "demand", "type": "reliability_divergence", "evidence_ids": ["ev_002"], "note": "y"},
        ],
        "open_question_context": [],
        "pass_id": 0,
    })
    result = retry_gate_node(_state(normalized_data=normalized, research_iteration=0))
    joined = " | ".join(result["retry_questions"]).lower()
    assert "valuation" in joined
    assert "demand" in joined
