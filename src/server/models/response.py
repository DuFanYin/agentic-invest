from pydantic import BaseModel, Field

from src.server.models.evidence import Evidence
from src.server.models.intent import ResearchIntent
from src.server.models.scenario import Scenario


class ValidationResult(BaseModel):
    is_valid: bool = True
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AgentStatus(BaseModel):
    agent: str
    status: str = "idle"
    action: str = "waiting"
    details: list[str] = Field(default_factory=list)


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
