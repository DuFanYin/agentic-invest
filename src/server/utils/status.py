"""Shared helper for mutating AgentStatus lists."""

from dataclasses import dataclass
from datetime import UTC, datetime

from src.server.models.response import AgentLifecycle, AgentPhase, AgentStatus


@dataclass(frozen=True)
class AgentMeta:
    name: str
    tag: str
    failed_phase: AgentPhase


_AGENT_STATUS_META: tuple[AgentMeta, ...] = (
    AgentMeta(name="planner", tag="O", failed_phase="planning"),
    AgentMeta(name="research", tag="R", failed_phase="collecting_evidence"),
    AgentMeta(name="fundamental_analysis", tag="F", failed_phase="analyzing_fundamentals"),
    AgentMeta(name="macro_analysis", tag="X", failed_phase="analyzing_macro"),
    AgentMeta(name="market_sentiment", tag="M", failed_phase="analyzing_sentiment"),
    AgentMeta(name="llm_judge", tag="G", failed_phase="evaluating_readiness"),
    AgentMeta(name="scenario_scoring", tag="S", failed_phase="scoring_scenarios"),
    AgentMeta(name="scenario_debate", tag="D", failed_phase="debating_scenarios"),
    AgentMeta(name="report_finalize", tag="V", failed_phase="generating_report"),
)

AGENT_NAMES: list[str] = [item.name for item in _AGENT_STATUS_META]
AGENT_TAG_BY_NODE: dict[str, str] = {item.name: item.tag for item in _AGENT_STATUS_META}
FAILED_PHASE_BY_AGENT: dict[str, AgentPhase] = {item.name: item.failed_phase for item in _AGENT_STATUS_META}


def initial_agent_statuses(*, running: str = "") -> list[AgentStatus]:
    now = datetime.now(UTC).isoformat()
    return [
        AgentStatus(
            agent=name,
            lifecycle="active" if name == running else "standby",
            phase="planning" if name == running else "idle",
            action="starting" if name == running else "waiting",
            details=[],
            entered_at=now,
            last_update_at=now,
            waiting_on=None,
            progress_hint=None,
            retry_count=0,
            max_retries=0,
            last_error=None,
        )
        for name in AGENT_NAMES
    ]


def mark_analysis_done(
    statuses: list[AgentStatus], node: str, phase: AgentPhase, action: str, details: list[str] | None = None
) -> list[AgentStatus]:
    """Mark an analysis node standby and signal llm_judge as active.

    Used by the three parallel analysis nodes (fundamental_analysis,
    macro_analysis, market_sentiment) which all end with the same two-step
    status update.
    """
    statuses = update_status(statuses, node, lifecycle="standby", phase=phase, action=action, details=details)
    return update_status(
        statuses, "llm_judge", lifecycle="active", phase="evaluating_readiness", action="reviewing analysis readiness"
    )


def _update_item(
    item: AgentStatus,
    agent: str,
    now: str,
    *,
    lifecycle: AgentLifecycle,
    phase: AgentPhase,
    action: str,
    details: list[str] | None,
    waiting_on: str | None,
    progress_hint: str | None,
    retry_count: int | None,
    max_retries: int | None,
    last_error: str | None,
) -> AgentStatus:
    if item.agent != agent:
        return item
    same_stage = item.lifecycle == lifecycle and item.phase == phase
    return AgentStatus(
        agent=item.agent,
        lifecycle=lifecycle,
        phase=phase,
        action=action,
        details=details or [],
        entered_at=item.entered_at if same_stage else now,
        last_update_at=now,
        waiting_on=waiting_on,
        progress_hint=progress_hint,
        retry_count=retry_count if retry_count is not None else item.retry_count,
        max_retries=max_retries if max_retries is not None else item.max_retries,
        last_error=last_error,
    )


def update_status(
    current: list[AgentStatus],
    agent: str,
    *,
    lifecycle: AgentLifecycle,
    phase: AgentPhase,
    action: str,
    details: list[str] | None = None,
    waiting_on: str | None = None,
    progress_hint: str | None = None,
    retry_count: int | None = None,
    max_retries: int | None = None,
    last_error: str | None = None,
) -> list[AgentStatus]:
    """Return a new list with one agent's entry replaced."""
    now = datetime.now(UTC).isoformat()
    return [
        _update_item(
            item,
            agent,
            now,
            lifecycle=lifecycle,
            phase=phase,
            action=action,
            details=details,
            waiting_on=waiting_on,
            progress_hint=progress_hint,
            retry_count=retry_count,
            max_retries=max_retries,
            last_error=last_error,
        )
        for item in current
    ]
