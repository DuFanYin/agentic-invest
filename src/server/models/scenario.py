from pydantic import BaseModel, Field


class Scenario(BaseModel):
    name: str
    description: str
    score: float = Field(..., ge=0, le=1)
    triggers: list[str] = Field(default_factory=list)
    signals: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
