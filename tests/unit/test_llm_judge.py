import asyncio
import json

from src.server.agents.llm_judge import (
    MAX_RESEARCH_ITERATIONS,
    llm_judge_node,
    llm_judge_router,
)
from src.server.models.analysis import NormalizedData
from src.server.models.intent import ResearchIntent


class _FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def call_with_retry(self, prompt, *, system, node):
        self.calls.append({"prompt": prompt, "system": system, "node": node})
        return self.responses.pop(0)


def _state(**overrides):
    base = {
        "intent": ResearchIntent(ticker="AAPL", subjects=["Apple"], scope="company", time_horizon="3 years"),
        "retry_questions": [],
        "research_iteration": 0,
        "normalized_data": None,
        "evidence": [{"source_type": "web"}],
        "agent_statuses": [],
    }
    base.update(overrides)
    return base


def test_router_retries_when_questions_present():
    assert llm_judge_router(_state(retry_questions=["q1"])) == "research"


def test_judge_clears_after_max_iterations():
    result = asyncio.run(
        llm_judge_node(
            _state(
                intent=ResearchIntent(ticker=None, subjects=["Apple"], scope="company", time_horizon=None),
                research_iteration=MAX_RESEARCH_ITERATIONS,
            )
        )
    )
    assert result["retry_questions"] == []
    assert result["retry_reason"] == "none"


def test_judge_triggers_structural_gap_when_ticker_missing():
    result = asyncio.run(
        llm_judge_node(
            _state(
                intent=ResearchIntent(ticker=None, subjects=["Apple"], scope="company", time_horizon=None),
                research_iteration=0,
            )
        )
    )
    assert result["retry_questions"] != []
    assert result["retry_reason"] == "structural"
    assert "ticker" in result["retry_questions"][0].lower()


def test_judge_triggers_conflict_retry_with_hint():
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
    llm = _FakeLLM([
        json.dumps({"should_retry": False, "retry_question": "", "reason": "robust"}),
        json.dumps({"should_retry": True, "retry_question": "resolve valuation conflict from filings", "reason": "material conflict"}),
    ])
    result = asyncio.run(
        llm_judge_node(
            _state(normalized_data=normalized, research_iteration=0),
            llm=llm,
        )
    )
    assert result["retry_reason"] == "evidence_conflict"
    assert result["retry_questions"] != []
    assert len(llm.calls) == 2


def test_judge_triggers_analysis_robustness_retry_from_first_judge():
    llm = _FakeLLM([
        json.dumps({"should_retry": True, "retry_question": "gather margin trend and guidance", "reason": "thin analysis"}),
    ])
    result = asyncio.run(llm_judge_node(_state(research_iteration=0), llm=llm))
    assert result["retry_reason"] == "analysis_robustness"
    assert result["retry_questions"] == ["gather margin trend and guidance"]
    assert len(llm.calls) == 1
