"""
LangGraph shared state.

evidence:        operator.add — each research pass appends new items.
agent_questions: operator.add — both parallel analysis nodes write their
                 missing-field questions in the same step; gap_check reads the
                 accumulated list then resets it to [] for the next cycle.
open_questions:  plain replace — gap_check writes the merged list each cycle;
                 accumulation would break the retry-loop termination check.
agent_statuses:  _last_list — fundamental_analysis and market_sentiment run in
                 parallel and both write this field in the same step; the plain
                 LastValue channel raises InvalidUpdateError on concurrent writes,
                 so we use a custom reducer that picks the last (non-empty) list.
"""

import operator
from typing import Annotated, Any

from typing_extensions import TypedDict

from src.server.models.evidence import Evidence
from src.server.models.intent import ResearchIntent
from src.server.models.response import AgentStatus, ValidationResult
from src.server.models.scenario import Scenario


def _last_list(left: list, right: list) -> list:
    """Reducer: return whichever list is non-empty (prefer right/latest)."""
    return right if right else left


class ResearchState(TypedDict, total=False):
    # ── Input ──────────────────────────────────────────────────────────────
    query: str

    # ── Orchestrator ───────────────────────────────────────────────────────
    intent: ResearchIntent | None

    # ── Research agent ─────────────────────────────────────────────────────
    evidence: Annotated[list[Evidence], operator.add]   # append across passes
    normalized_data: dict[str, Any]

    # ── Analysis agents ────────────────────────────────────────────────────
    fundamental_analysis: dict[str, Any]
    market_sentiment: dict[str, Any]

    # ── Gap / retry tracking ───────────────────────────────────────────────
    # agent_questions: accumulated by analysis nodes within a pass; gap_check
    # reads and resets each cycle.
    agent_questions: Annotated[list[str], operator.add]
    open_questions: list[str]   # replaced each cycle — do NOT use operator.add
    research_pass: int          # incremented by research_node; read by gap_check

    # ── Scenario scoring ───────────────────────────────────────────────────
    scenarios: list[Scenario]

    # ── Report & validation ────────────────────────────────────────────────
    report_markdown: str
    report_json: dict[str, Any]
    validation_result: ValidationResult

    # ── Agent status sidebar ───────────────────────────────────────────────
    # _last_list: both parallel analysis nodes write this in the same step.
    agent_statuses: Annotated[list[AgentStatus], _last_list]
