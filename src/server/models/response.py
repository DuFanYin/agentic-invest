from typing import Literal, TypeAlias

from pydantic import BaseModel, Field
AgentLifecycle: TypeAlias = Literal[
    "standby",
    "active",
    "waiting",
    "blocked",
    "degraded",  # best-effort: node ran but returned a degraded result, pipeline continues
    "failed",    # hard failure: node raised, pipeline stopped
]

AgentPhase: TypeAlias = Literal[
    "idle",
    "planning",
    "dispatching",
    "collecting_evidence",
    "retrying_evidence",
    "analyzing_fundamentals",
    "analyzing_macro",
    "analyzing_sentiment",
    "evaluating_gaps",
    "gap_retry_required",
    "gap_resolved",
    "scoring_scenarios",
    "debating_scenarios",
    "generating_report",
    "workflow_complete",
]


from src.server.models.evidence import Evidence
from src.server.models.intent import ResearchIntent
from src.server.models.analysis import (
    FundamentalAnalysis,
    MacroAnalysis,
    MarketSentiment,
    ScenarioDebate,
)
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


LLMCallStatus: TypeAlias = Literal["calling", "success", "retry", "failed"]


class LLMCall(BaseModel):
    id: str
    node: str
    agent_tag: str
    model: str
    attempt: int
    status: LLMCallStatus
    latency_ms: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None


class ResearchResponse(BaseModel):
    report_markdown: str
    report_json: dict = Field(default_factory=dict)
    intent: ResearchIntent | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    fundamental_analysis: FundamentalAnalysis | None = None
    macro_analysis: MacroAnalysis | None = None
    market_sentiment: MarketSentiment | None = None
    scenarios: list[Scenario] = Field(default_factory=list)
    scenario_debate: ScenarioDebate | None = None
    agent_statuses: list[AgentStatus] = Field(default_factory=list)
    validation_result: ValidationResult = Field(default_factory=ValidationResult)
    llm_calls: list[LLMCall] = Field(default_factory=list)
    total_cost_usd: float = 0.0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    narrative_sections: dict[str, str] = Field(default_factory=dict)
