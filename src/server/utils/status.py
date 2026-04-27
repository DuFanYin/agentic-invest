"""Shared helper for mutating AgentStatus lists."""

from datetime import UTC, datetime

from src.server.models.response import AgentLifecycle, AgentPhase, AgentStatus

AGENT_NAMES = [
    "parse_intent",
    "research",
    "fundamental_analysis",
    "market_sentiment",
    "gap_check",
    "scenario_scoring",
    "report_verification",
]


def initial_agent_statuses(*, running: str = "") -> list[AgentStatus]:
    now = datetime.now(UTC).isoformat()
    return [
        AgentStatus(
            agent=name,
            lifecycle="active" if name == running else "standby",
            phase="planning" if name == running else "idle",
            action="parsing query" if name == running else "waiting",
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
        AgentStatus(
            agent=item.agent,
            lifecycle=lifecycle if item.agent == agent else item.lifecycle,
            phase=phase if item.agent == agent else item.phase,
            action=action if item.agent == agent else item.action,
            details=(details if item.agent == agent else item.details) or [],
            entered_at=(
                (
                    item.entered_at
                    if item.lifecycle == lifecycle and item.phase == phase
                    else now
                )
                if item.agent == agent
                else item.entered_at
            ),
            last_update_at=now if item.agent == agent else item.last_update_at,
            waiting_on=waiting_on if item.agent == agent else item.waiting_on,
            progress_hint=progress_hint if item.agent == agent else item.progress_hint,
            retry_count=(retry_count if retry_count is not None else item.retry_count)
            if item.agent == agent
            else item.retry_count,
            max_retries=(max_retries if max_retries is not None else item.max_retries)
            if item.agent == agent
            else item.max_retries,
            last_error=last_error if item.agent == agent else item.last_error,
        )
        for item in current
    ]
