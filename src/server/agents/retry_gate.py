"""Retry gate agent.

Boundary:
- This agent decides retries only from evidence adequacy signals
  (structural gaps, analysis follow-up questions, conflict signals).
- It does NOT evaluate report delivery quality.
"""

from __future__ import annotations

from src.server.models.state import ResearchState, _RESET
from src.server.utils.status import update_status

MAX_RESEARCH_ITERATIONS = 2


def retry_gate_node(state: ResearchState) -> ResearchState:
    intent = state.get("intent")
    normalized_data = state.get("normalized_data")
    current_iteration = state.get("research_iteration", 1)
    statuses = list(state.get("agent_statuses") or [])

    # Structural checks
    new_questions: list[str] = []
    scope = (intent.scope if intent else "").lower()
    if intent and scope == "company" and not intent.ticker:
        new_questions.append("Need clearer company/ticker mapping from query context")
    if intent and scope == "company" and not intent.time_horizon:
        new_questions.append("Need explicit investment horizon to refine scenario assumptions")

    # Agent-sourced questions from parallel analysis nodes
    agent_questions: list[str] = list(state.get("agent_questions") or [])
    new_questions.extend(agent_questions)

    # Conflict signals from research
    conflicts = normalized_data.conflicts if normalized_data else []
    if conflicts:
        new_questions.append(
            f"Conflicting evidence detected across {len(conflicts)} topic(s): "
            + ", ".join(getattr(c, "topic", str(c)) for c in conflicts)
            + ". Supplementary research may resolve these."
        )

    # Cap retries
    if current_iteration >= MAX_RESEARCH_ITERATIONS:
        new_questions = []

    will_retry = bool(new_questions)

    if statuses:
        statuses = update_status(
            statuses, "retry_gate",
            lifecycle="waiting" if will_retry else "standby",
            phase="gap_retry_required" if will_retry else "gap_resolved",
            action="retrying research" if will_retry else "gaps resolved",
            details=[
                f"retry_questions={len(new_questions)}",
                f"agent_sourced={len(agent_questions)}",
                f"conflicts={len(conflicts)}",
            ],
        )
        if will_retry:
            statuses = update_status(
                statuses, "research",
                lifecycle="active", phase="retrying_evidence", action="supplementary evidence collection",
            )
        else:
            statuses = update_status(
                statuses, "scenario_scoring",
                lifecycle="active", phase="scoring_scenarios", action="scoring scenarios",
            )

    return {
        "retry_questions": new_questions,
        "agent_questions": [_RESET],  # sentinel: reducer clears list for next cycle
        "agent_statuses": statuses,
    }


def retry_router(state: ResearchState) -> str:
    if state.get("retry_questions"):
        return "research"
    return "scenario_scoring"
