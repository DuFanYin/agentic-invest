"""Shared helper for mutating AgentStatus lists."""

from src.server.models.response import AgentStatus

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
    return [
        AgentStatus(
            agent=name,
            status="running" if name == running else "idle",
            action="parsing query" if name == running else "waiting",
            details=[],
        )
        for name in AGENT_NAMES
    ]


def update_status(
    current: list[AgentStatus],
    agent: str,
    *,
    status: str,
    action: str,
    details: list[str] | None = None,
) -> list[AgentStatus]:
    """Return a new list with one agent's entry replaced."""
    return [
        AgentStatus(
            agent=item.agent,
            status=status if item.agent == agent else item.status,
            action=action if item.agent == agent else item.action,
            details=(details if item.agent == agent else item.details) or [],
        )
        for item in current
    ]
