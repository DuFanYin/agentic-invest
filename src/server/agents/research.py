"""Research node — collects evidence and normalises raw data into ResearchState."""

from datetime import UTC, datetime

from src.server.models.evidence import Evidence
from src.server.models.state import ResearchState
from src.server.utils.status import update_status


def research_node(state: ResearchState) -> ResearchState:
    query = state["query"]
    intent = state.get("intent")
    open_questions: list[str] = state.get("open_questions") or []
    pass_id: int = state.get("research_pass", 0)
    statuses = list(state.get("agent_statuses") or [])

    retrieved_at = datetime.now(UTC).isoformat()
    id_offset = pass_id * 100

    new_evidence: list[Evidence] = [
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
            summary="Dummy normalised financial metrics with 3 time slices.",
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
    ]

    normalized_data: dict = {
        "query": query,
        "intent": intent.model_dump() if intent else {},
        "metrics": {
            "ttm": {"revenue_growth_pct": 14.2, "gross_margin_pct": 58.1},
            "three_year_avg": {"revenue_growth_pct": 18.4, "operating_margin_pct": 25.0},
            "latest_quarter": {"revenue_growth_pct": 11.0, "inventory_turnover": 4.2},
        },
        "missing_fields": [],
        "conflicts": [],
        "open_question_context": open_questions,
        "pass_id": pass_id,
    }

    if statuses:
        statuses = update_status(
            statuses, "research",
            status="completed", action="evidence collected",
            details=[f"evidence=3", f"pass={pass_id}"],
        )
        statuses = update_status(
            statuses, "fundamental_analysis",
            status="running", action="analysing fundamentals",
        )
        statuses = update_status(
            statuses, "market_sentiment",
            status="running", action="analysing sentiment",
        )

    return {
        "evidence": new_evidence,
        "normalized_data": normalized_data,
        "research_pass": pass_id + 1,
        "agent_statuses": statuses,
    }
