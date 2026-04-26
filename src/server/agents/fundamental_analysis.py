"""Fundamental analysis node."""

from src.server.models.state import ResearchState
from src.server.utils.status import update_status


def fundamental_analysis_node(state: ResearchState) -> ResearchState:
    evidence = state.get("evidence") or []
    normalized_data = state.get("normalized_data") or {}
    evidence_ids = [ev.id for ev in evidence]
    statuses = list(state.get("agent_statuses") or [])

    result = {
        "agent": "fundamental_analysis",
        "claims": [
            {
                "statement": "Core business quality remains resilient in the base case.",
                "confidence": "medium",
                "evidence_ids": evidence_ids[:2],
            },
            {
                "statement": "Valuation is sensitive to growth deceleration assumptions.",
                "confidence": "medium",
                "evidence_ids": evidence_ids[1:],
            },
        ],
        "metrics": normalized_data.get("metrics", {}),
        "missing_fields": normalized_data.get("missing_fields", []),
        "business_quality": {
            "view": "stable",
            "drivers": ["competitive position", "execution quality"],
        },
        "financials": {
            "profitability_trend": "flat_to_improving",
            "cash_flow_quality": "moderate",
        },
        "valuation": {
            "relative_multiple_view": "near historical median",
            "simplified_dcf_view": "fair_value_range_wide",
        },
        "fundamental_risks": [
            {
                "name": "Demand slowdown",
                "impact": "medium",
                "signal": "order growth below trailing average",
                "evidence_ids": [evidence_ids[-1]] if evidence_ids else [],
            },
            {
                "name": "Margin pressure",
                "impact": "medium",
                "signal": "gross margin trend down two consecutive quarters",
                "evidence_ids": evidence_ids[:1],
            },
        ],
    }

    if statuses:
        statuses = update_status(
            statuses, "fundamental_analysis",
            status="completed", action="fundamentals ready",
            details=[f"claims={len(result['claims'])}"],
        )

    return {"fundamental_analysis": result, "agent_statuses": statuses}
