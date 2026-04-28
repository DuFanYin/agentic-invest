import asyncio
import json

from src.server.agents.llm_judge import (
    MAX_RESEARCH_ITERATIONS,
    llm_judge_node,
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
        "intent": ResearchIntent(
            ticker="AAPL", subjects=["Apple"], scope="company", time_horizon="3 years"
        ),
        "retry_questions": [],
        "research_iteration": 0,
        "normalized_data": None,
        "evidence": [{"source_type": "web"}],
        "agent_statuses": [],
    }
    base.update(overrides)
    return base


def test_judge_clears_after_max_iterations():
    result = asyncio.run(
        llm_judge_node(
            _state(
                intent=ResearchIntent(
                    ticker=None, subjects=["Apple"], scope="company", time_horizon=None
                ),
                research_iteration=MAX_RESEARCH_ITERATIONS,
            )
        )
    )
    pd = result["policy_decision"]
    assert pd.reason_code == "none"
    assert pd.retry_question == ""


def test_judge_triggers_structural_gap_when_ticker_missing():
    result = asyncio.run(
        llm_judge_node(
            _state(
                intent=ResearchIntent(
                    ticker=None, subjects=["Apple"], scope="company", time_horizon=None
                ),
                research_iteration=0,
            )
        )
    )
    pd = result["policy_decision"]
    assert pd.reason_code == "structural"
    assert "ticker" in pd.retry_question.lower()
    assert pd.action == "retry_full_research"


def test_judge_triggers_conflict_retry_with_hint():
    normalized = NormalizedData.model_validate(
        {
            "query": "Analyse AAPL",
            "intent": {},
            "metrics": {},
            "missing_fields": [],
            "conflicts": [
                {
                    "topic": "valuation",
                    "type": "reliability_divergence",
                    "evidence_ids": ["ev_001"],
                    "note": "x",
                },
                {
                    "topic": "demand",
                    "type": "reliability_divergence",
                    "evidence_ids": ["ev_002"],
                    "note": "y",
                },
            ],
            "open_question_context": [],
            "pass_id": 0,
        }
    )
    llm = _FakeLLM(
        [
            json.dumps(
                {"should_retry": False, "retry_question": "", "reason": "robust"}
            ),
            json.dumps(
                {
                    "should_retry": True,
                    "retry_question": "resolve valuation conflict from filings",
                    "reason": "material conflict",
                }
            ),
        ]
    )
    result = asyncio.run(
        llm_judge_node(
            _state(normalized_data=normalized, research_iteration=0),
            llm=llm,
        )
    )
    pd = result["policy_decision"]
    assert pd.reason_code == "evidence_conflict"
    assert pd.retry_question != ""
    assert len(llm.calls) == 2


def test_judge_triggers_analysis_robustness_retry_from_first_judge():
    llm = _FakeLLM(
        [
            json.dumps(
                {
                    "should_retry": True,
                    "retry_question": "gather margin trend and guidance",
                    "reason": "thin analysis",
                }
            ),
        ]
    )
    result = asyncio.run(llm_judge_node(_state(research_iteration=0), llm=llm))
    pd = result["policy_decision"]
    assert pd.reason_code == "analysis_robustness"
    assert pd.retry_question == "gather margin trend and guidance"
    assert len(llm.calls) == 1


def test_judge_degraded_when_llm_fails():
    class _FailLLM:
        async def call_with_retry(self, prompt, *, system, node):
            raise RuntimeError("all models exhausted")

    result = asyncio.run(llm_judge_node(_state(research_iteration=0), llm=_FailLLM()))
    pd = result["policy_decision"]
    assert pd.reason_code == "judge_degraded"
    assert pd.retry_question == ""
