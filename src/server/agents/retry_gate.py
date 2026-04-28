"""Retry gate — deterministic routing based on structural gaps and evidence conflicts.

Retries only when there is a concrete reason to believe new evidence can be obtained:
  - structural: intent lacks ticker (company scope cannot proceed without it)
  - evidence_conflict: normalized_data.conflicts present (supplementary search may resolve)

LLM missing_fields (analysis wishlist) no longer trigger retries — they are recorded
in the report as data limitations, not routed back to research.
"""

from __future__ import annotations

from src.server.models.state import ResearchState
from src.server.utils.contract import NODE_CONTRACTS, assert_writes
from src.server.utils.status import update_status

_READS  = NODE_CONTRACTS["retry_gate"].reads
_WRITES = NODE_CONTRACTS["retry_gate"].writes

MAX_RESEARCH_ITERATIONS = 2


def retry_gate_node(state: ResearchState) -> ResearchState:

    intent = state.get("intent")
    normalized_data = state.get("normalized_data")
    current_iteration = state.get("research_iteration", 1)
    statuses = list(state.get("agent_statuses") or [])

    retry_question: str | None = None
    retry_reason: str = "none"

    if current_iteration < MAX_RESEARCH_ITERATIONS:
        scope = (intent.scope if intent else "").lower()

        # Structural gap: company scope without ticker — research cannot proceed correctly
        if intent and scope == "company" and not intent.ticker:
            retry_question = "Need clearer company/ticker mapping from query context"
            retry_reason = "structural"

        # Evidence conflict: contradictory signals across sources
        elif normalized_data and normalized_data.conflicts:
            topics = ", ".join(
                getattr(c, "topic", str(c)) for c in normalized_data.conflicts
            )
            retry_question = f"Conflicting evidence on: {topics} — search for authoritative source"
            retry_reason = "evidence_conflict"

    will_retry = retry_question is not None

    if statuses:
        statuses = update_status(
            statuses, "retry_gate",
            lifecycle="waiting" if will_retry else "standby",
            phase="gap_retry_required" if will_retry else "gap_resolved",
            action="retrying research" if will_retry else "gaps resolved",
            details=[f"reason={retry_reason}"],
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

    delta = {
        "retry_questions": [retry_question] if will_retry else [],
        "retry_reason": retry_reason,
        "agent_statuses": statuses,
    }
    assert_writes(delta, _WRITES, "retry_gate")
    return delta


def retry_router(state: ResearchState) -> str:
    if state.get("retry_questions"):
        return "research"
    return "scenario_scoring"
