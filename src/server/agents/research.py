from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.server.models.evidence import Evidence
from src.server.models.intent import ResearchIntent


@dataclass
class ResearchResult:
    evidence: list[Evidence] = field(default_factory=list)
    normalized_data: dict = field(default_factory=dict)


class ResearchAgent:
    def run(
        self,
        query: str,
        intent: ResearchIntent,
        *,
        open_questions: list[str] | None = None,
        pass_id: int = 0,
    ) -> ResearchResult:
        retrieved_at = datetime.now(UTC).isoformat()
        id_offset = pass_id * 100
        refinement_notes = open_questions or []

        return ResearchResult(
            evidence=[
                Evidence(
                    id=f"ev_{id_offset + 1:03d}",
                    source_type="filing",
                    title="Company filing placeholder",
                    url="https://example.com/company-filing",
                    published_at="2025-12-31T00:00:00Z",
                    retrieved_at=retrieved_at,
                    summary=f"Dummy filing-derived signal for query: {query}",
                    reliability="high",
                    related_topics=["revenue", "margin", "risk"],
                ),
                Evidence(
                    id=f"ev_{id_offset + 2:03d}",
                    source_type="financial_api",
                    title="Financial metrics placeholder",
                    url="https://example.com/financial-api",
                    published_at="2026-01-15T00:00:00Z",
                    retrieved_at=retrieved_at,
                    summary="Dummy normalized financial metrics with 3 time slices.",
                    reliability="high",
                    related_topics=["ttm", "3y", "latest_quarter"],
                ),
                Evidence(
                    id=f"ev_{id_offset + 3:03d}",
                    source_type="news",
                    title="Industry news placeholder",
                    url="https://example.com/industry-news",
                    published_at="2026-02-01T00:00:00Z",
                    retrieved_at=retrieved_at,
                    summary="Dummy news context for demand and regulatory changes.",
                    reliability="medium",
                    related_topics=["demand", "regulation", "competition"],
                ),
            ],
            normalized_data={
                "query": query,
                "intent": intent.model_dump(),
                "metrics": {
                    "ttm": {"revenue_growth_pct": 14.2, "gross_margin_pct": 58.1},
                    "three_year_avg": {"revenue_growth_pct": 18.4, "operating_margin_pct": 25.0},
                    "latest_quarter": {"revenue_growth_pct": 11.0, "inventory_turnover": 4.2},
                },
                "missing_fields": [],
                "conflicts": [],
                "open_question_context": refinement_notes,
                "pass_id": pass_id,
            },
        )
