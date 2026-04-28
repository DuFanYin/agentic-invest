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
        "retry_questions": [],
        "research_iteration": 0,
        "normalized_data": None,
        "agent_statuses": [],
    }
    base.update(overrides)
    return base


def test_router_retries_when_questions_present():
    assert retry_router(_state(retry_questions=["q1"])) == "research"


def test_gate_clears_after_max_iterations():
    result = retry_gate_node(
        _state(
            intent=ResearchIntent(ticker=None, subjects=["Apple"], scope="company", time_horizon=None),
            research_iteration=MAX_RESEARCH_ITERATIONS,
        )
    )
    assert result["retry_questions"] == []
    assert result["retry_reason"] == "none"


def test_gate_triggers_structural_gap_when_ticker_missing():
    result = retry_gate_node(
        _state(
            intent=ResearchIntent(ticker=None, subjects=["Apple"], scope="company", time_horizon=None),
            research_iteration=0,
        )
    )
    assert result["retry_questions"] != []
    assert result["retry_reason"] == "structural"
    assert "ticker" in result["retry_questions"][0].lower()


def test_gate_triggers_conflict_retry_with_hint():
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
    assert result["retry_reason"] == "evidence_conflict"
    assert result["retry_questions"] != []
