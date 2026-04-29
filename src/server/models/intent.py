from pydantic import BaseModel, Field


class ResearchIntent(BaseModel):
    intent: str = "investment_research"
    subjects: list[str] = Field(default_factory=list)
    scope: str = "unknown"
    ticker: str | None = None
    time_horizon: str | None = None
