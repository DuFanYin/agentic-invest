from typing import Literal, TypeAlias

from pydantic import BaseModel, Field
AgentLifecycle: TypeAlias = Literal[
    "standby",
    "active",
    "waiting",
    "blocked",
    "failed",
]

AgentPhase: TypeAlias = Literal[
    "idle",
    "planning",
    "dispatching",
    "collecting_evidence",
    "retrying_evidence",
    "analyzing_fundamentals",
    "analyzing_sentiment",
    "evaluating_gaps",
    "gap_retry_required",
    "gap_resolved",
    "scoring_scenarios",
    "generating_report",
    "workflow_complete",
]


from src.server.models.evidence import Evidence
from src.server.models.intent import ResearchIntent
from src.server.models.scenario import Scenario


class ValidationResult(BaseModel):
    is_valid: bool = True
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AgentStatus(BaseModel):
    agent: str
    lifecycle: AgentLifecycle = "standby"
    phase: AgentPhase = "idle"
    action: str = "waiting"
    details: list[str] = Field(default_factory=list)
    entered_at: str | None = None
    last_update_at: str | None = None
    waiting_on: str | None = None
    progress_hint: str | None = None
    retry_count: int = 0
    max_retries: int = 0
    last_error: str | None = None


class ResearchResponse(BaseModel):
    report_markdown: str
    report_json: dict = Field(default_factory=dict)
    intent: ResearchIntent | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    fundamental_analysis: dict = Field(default_factory=dict)
    market_sentiment: dict = Field(default_factory=dict)
    scenarios: list[Scenario] = Field(default_factory=list)
    agent_statuses: list[AgentStatus] = Field(default_factory=list)
    validation_result: ValidationResult = Field(default_factory=ValidationResult)
