import asyncio
import json

from src.server.agents.llm_judge import MAX_RESEARCH_ITERATIONS, llm_judge_node
from src.server.models.analysis import FundamentalAnalysis, MacroAnalysis, MarketSentiment, NormalizedData
from src.server.models.intent import ResearchIntent
from src.server.utils.status import initial_agent_statuses


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


def test_judge_clears_after_max_iterations():
    result = asyncio.run(
        llm_judge_node(
            _state(
                intent=ResearchIntent(ticker=None, subjects=["Apple"], scope="company", time_horizon=None),
                research_iteration=MAX_RESEARCH_ITERATIONS,
            )
        )
    )
    pd = result["policy_decision"]
    assert pd.reason_code == "iteration_limit"
    assert pd.retry_question == ""
    assert result["retry_questions"] == []


def test_judge_at_iteration_cap_skips_all_llm_calls_even_with_conflicts():
    """Regression: do not run analysis/conflict judges after final research pass."""
    normalized = NormalizedData.model_validate(
        {
            "query": "Analyse AAPL",
            "intent": {},
            "metrics": {},
            "missing_fields": [],
            "conflicts": [
                {"topic": "valuation", "type": "reliability_divergence", "evidence_ids": ["ev_001"], "note": "x"}
            ],
            "open_question_context": [],
            "pass_id": 0,
        }
    )
    llm = _FakeLLM([json.dumps({"should_retry": True, "retry_question": "nope", "reason": "x"})])
    result = asyncio.run(
        llm_judge_node(_state(normalized_data=normalized, research_iteration=MAX_RESEARCH_ITERATIONS), llm=llm)
    )
    assert len(llm.calls) == 0
    assert result["policy_decision"].reason_code == "iteration_limit"


def test_judge_triggers_structural_gap_when_ticker_missing():
    result = asyncio.run(
        llm_judge_node(
            _state(
                intent=ResearchIntent(ticker=None, subjects=["Apple"], scope="company", time_horizon=None),
                research_iteration=0,
            )
        )
    )
    pd = result["policy_decision"]
    assert pd.reason_code == "structural"
    assert "ticker" in pd.retry_question.lower()
    assert pd.action == "retry_full_research"
    assert result["retry_questions"] == [pd.retry_question]


def test_judge_triggers_conflict_retry_with_hint():
    normalized = NormalizedData.model_validate(
        {
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
        }
    )
    llm = _FakeLLM(
        [
            json.dumps({"should_retry": False, "retry_question": "", "reason": "robust"}),
            json.dumps(
                {
                    "should_retry": True,
                    "retry_question": "resolve valuation conflict from filings",
                    "reason": "material conflict",
                }
            ),
        ]
    )
    result = asyncio.run(llm_judge_node(_state(normalized_data=normalized, research_iteration=0), llm=llm))
    pd = result["policy_decision"]
    assert pd.reason_code == "evidence_conflict"
    assert pd.retry_question != ""
    assert result["retry_scope"] == ["cap.fetch_web"]
    assert len(llm.calls) == 2


def test_judge_triggers_analysis_robustness_retry_from_first_judge():
    llm = _FakeLLM(
        [
            json.dumps(
                {"should_retry": True, "retry_question": "gather margin trend and guidance", "reason": "thin analysis"}
            )
        ]
    )
    result = asyncio.run(llm_judge_node(_state(research_iteration=0), llm=llm))
    pd = result["policy_decision"]
    assert pd.reason_code == "analysis_robustness"
    assert pd.retry_question == "gather margin trend and guidance"
    assert result["retry_scope"] is None
    assert len(llm.calls) == 1


def test_judge_halting_when_all_analyses_degraded_signals_report_finalize():
    """Policy halts before scenarios; status should not claim scenario_scoring is active."""
    llm = _FakeLLM([json.dumps({"should_retry": False, "retry_question": "", "reason": "ok"})])
    result = asyncio.run(
        llm_judge_node(
            _state(
                research_iteration=0,
                fundamental_analysis=FundamentalAnalysis(degraded=True),
                macro_analysis=MacroAnalysis(degraded=True),
                market_sentiment=MarketSentiment(degraded=True),
                agent_statuses=initial_agent_statuses(running="llm_judge"),
            ),
            llm=llm,
        )
    )
    assert result["policy_decision"].action == "halt_with_degraded_output"
    assert result["policy_decision"].reason_code == "all_degraded"
    statuses = result["agent_statuses"]
    rf = next(s for s in statuses if s.agent == "report_finalize")
    assert rf.phase == "generating_report"
    assert "skipping scenario" in rf.action


def test_judge_degraded_when_llm_fails():
    class _FailLLM:
        async def call_with_retry(self, prompt, *, system, node):
            raise RuntimeError("all models exhausted")

    result = asyncio.run(llm_judge_node(_state(research_iteration=0), llm=_FailLLM()))
    pd = result["policy_decision"]
    assert pd.reason_code == "judge_degraded"
    assert pd.retry_question == ""
