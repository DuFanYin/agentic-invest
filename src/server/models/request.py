from pydantic import BaseModel, Field, field_validator


class ResearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)

    @field_validator("query", mode="before")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        if not isinstance(value, str):
            return value
        return value.strip()

    @field_validator("query")
    @classmethod
    def validate_query_not_blank(cls, value: str) -> str:
        if not value:
            raise ValueError("query must not be blank")
        return value
