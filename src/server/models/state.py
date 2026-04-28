"""
LangGraph shared state.

evidence:        operator.add — each research iteration appends new items.
retry_questions: plain replace — llm_judge writes a single-item list each cycle;
                 accumulation would break the retry-loop termination check.
agent_statuses:  _last_list — fundamental_analysis and market_sentiment run in
                 parallel and both write this field in the same step; the plain
                 LastValue channel raises InvalidUpdateError on concurrent writes,
                 so we use a custom reducer that picks the last (non-empty) list.
"""

import operator
from datetime import datetime
from typing import Annotated, Any

from typing_extensions import TypedDict

from src.server.models.analysis import (
    Budget,
    FundamentalAnalysis,
    MacroAnalysis,
    MarketSentiment,
    NormalizedData,
    PlanContext,
    QualityMetrics,
    ScenarioDebate,
)
from src.server.models.evidence import Evidence
from src.server.models.intent import ResearchIntent
from src.server.models.response import AgentStatus, ValidationResult
from src.server.models.scenario import Scenario


def _last_list(left: list, right: list) -> list:
    """
    Reducer for parallel `agent_statuses` writes.
    Merge by `agent` so updates from concurrent nodes are preserved.
    Prefer the most recently updated status for each agent and use lifecycle
    rank as a deterministic tie-breaker when timestamps are identical.
    """
    lifecycle_rank = {
        "standby": 0,
        "active": 1,
        "waiting": 2,
        "blocked": 3,
        "failed": 4,
    }

    def _ts(item) -> float:
        value = getattr(item, "last_update_at", None)
        if not value:
            return 0.0
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0.0

    if not left:
        return right
    if not right:
        return left

    merged = {item.agent: item for item in left}
    for item in right:
        prev = merged.get(item.agent)
        if prev is None:
            merged[item.agent] = item
            continue
        prev_ts = _ts(prev)
        new_ts = _ts(item)
        if new_ts > prev_ts:
            merged[item.agent] = item
            continue
        if new_ts < prev_ts:
            continue
        prev_rank = lifecycle_rank.get(getattr(prev, "lifecycle", ""), 0)
        new_rank = lifecycle_rank.get(getattr(item, "lifecycle", ""), 0)
        merged[item.agent] = item if new_rank >= prev_rank else prev
    return list(merged.values())


class ResearchState(TypedDict, total=False):
    # ── Input ──────────────────────────────────────────────────────────────
    query: str

    # ── Orchestrator ───────────────────────────────────────────────────────
    intent: ResearchIntent | None
    plan_context: PlanContext | None  # consolidated planning output (replaces 4 scattered fields)

    # ── Research agent ─────────────────────────────────────────────────────
    evidence: Annotated[list[Evidence], operator.add]   # append across passes
    normalized_data: NormalizedData

    # ── Analysis agents ────────────────────────────────────────────────────
    fundamental_analysis: FundamentalAnalysis
    macro_analysis: MacroAnalysis
    market_sentiment: MarketSentiment

    # ── Gap / retry tracking ───────────────────────────────────────────────
    retry_questions: list[str]  # replaced each cycle — do NOT use operator.add
    retry_reason: str           # "structural" | "evidence_conflict" | "none"
    research_iteration: int     # incremented by research_node; read by llm_judge

    # ── Scenario scoring & debate ──────────────────────────────────────────
    scenarios: list[Scenario]
    scenario_debate: ScenarioDebate

    # ── Report & validation ────────────────────────────────────────────────
    narrative_sections: dict[str, str]   # section_id → LLM-written markdown
    report_markdown: str                 # full export document
    report_json: dict[str, Any]
    validation_result: ValidationResult
    quality_metrics: QualityMetrics
    stop_reason: str

    # ── Budget ─────────────────────────────────────────────────────────────
    budget: Budget

    # ── Agent status sidebar ───────────────────────────────────────────────
    # _last_list: both parallel analysis nodes write this in the same step.
    agent_statuses: Annotated[list[AgentStatus], _last_list]
