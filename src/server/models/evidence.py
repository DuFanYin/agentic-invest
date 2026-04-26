from pydantic import BaseModel, Field


class Evidence(BaseModel):
    id: str
    source_type: str
    title: str
    url: str | None = None
    published_at: str | None = None
    retrieved_at: str | None = None
    summary: str
    reliability: str = "medium"
    related_topics: list[str] = Field(default_factory=list)
