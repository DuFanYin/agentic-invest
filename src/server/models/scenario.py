from __future__ import annotations

from pydantic import BaseModel, Field


class Scenario(BaseModel):
    id: str = ""
    name: str
    description: str
    probability: float = Field(..., ge=0, le=1)
    drivers: list[str] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(..., min_length=1)
