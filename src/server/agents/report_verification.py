"""Report generation and verification node."""

from src.server.models.response import ValidationResult
from src.server.models.scenario import Scenario
from src.server.models.state import ResearchState
from src.server.utils.status import update_status
from src.server.utils.validation import (
    validate_claim_coverage,
    validate_evidence_completeness,
    validate_scenario_scores,
)


def report_verification_node(state: ResearchState) -> ResearchState:
    intent = state.get("intent")
    evidence = state.get("evidence") or []
    fundamental_analysis = state.get("fundamental_analysis") or {}
    market_sentiment = state.get("market_sentiment") or {}
    scenarios: list[Scenario] = state.get("scenarios") or []
    statuses = list(state.get("agent_statuses") or [])

    evidence_dump = [item.model_dump() for item in evidence]
    available_evidence_ids = {item["id"] for item in evidence_dump}

    errors: list[str] = []
    errors.extend(validate_scenario_scores(scenarios))
    errors.extend(validate_evidence_completeness(evidence_dump))
    errors.extend(validate_claim_coverage(fundamental_analysis, available_evidence_ids))
    errors.extend(validate_claim_coverage(market_sentiment, available_evidence_ids))

    warnings: list[str] = []
    if fundamental_analysis.get("missing_fields"):
        warnings.append(
            f"Missing fields reported: {', '.join(fundamental_analysis['missing_fields'])}"
        )
    if market_sentiment.get("missing_fields"):
        warnings.append(
            f"Missing sentiment fields reported: {', '.join(market_sentiment['missing_fields'])}"
        )

    report_markdown = _build_markdown_report(
        intent=intent,
        evidence=evidence_dump,
        fundamental_analysis=fundamental_analysis,
        market_sentiment=market_sentiment,
        scenarios=scenarios,
    )

    report_json = {
        "intent": intent.model_dump() if intent else {},
        "evidence": evidence_dump,
        "fundamental_analysis": fundamental_analysis,
        "market_sentiment": market_sentiment,
        "scenarios": [s.model_dump() for s in scenarios],
        "validation": {"errors": errors, "warnings": warnings},
    }

    if statuses:
        statuses = update_status(
            statuses, "report_verification",
            status="completed", action="report published",
            details=[f"is_valid={not errors}", f"errors={len(errors)}"],
        )

    return {
        "report_markdown": report_markdown,
        "report_json": report_json,
        "validation_result": ValidationResult(
            is_valid=not errors, errors=errors, warnings=warnings
        ),
        "agent_statuses": statuses,
    }


# ── helpers ────────────────────────────────────────────────────────────────

def _build_markdown_report(
    *,
    intent,
    evidence: list[dict],
    fundamental_analysis: dict,
    market_sentiment: dict,
    scenarios: list[Scenario],
) -> str:
    subjects = ", ".join(intent.subjects) if intent and intent.subjects else "N/A"
    lines: list[str] = [
        "# Executive Summary",
        f"- Intent: {intent.intent if intent else 'N/A'}",
        f"- Scope: {intent.scope if intent else 'N/A'}",
        f"- Subjects: {subjects}",
        "",
        "## Company / Theme Overview",
        "Multi-agent investment research output with structured evidence, "
        "fundamental analysis, market sentiment, and scenario scoring.",
        "",
        "## Key Evidence",
    ]
    for item in evidence:
        lines.append(f"- {item['id']}: {item['title']} ({item.get('url', 'N/A')})")

    lines.extend(
        [
            "",
            "## Fundamental Analysis",
            f"- Business quality: {fundamental_analysis.get('business_quality', {}).get('view', 'N/A')}",
            f"- Profitability trend: {fundamental_analysis.get('financials', {}).get('profitability_trend', 'N/A')}",
            f"- Valuation: {fundamental_analysis.get('valuation', {}).get('relative_multiple_view', 'N/A')}",
            "",
            "## Market Sentiment",
            f"- Direction: {market_sentiment.get('news_sentiment', {}).get('direction', 'N/A')}",
            f"- Narrative: {market_sentiment.get('market_narrative', {}).get('summary', 'N/A')}",
            "",
            "## Valuation View",
            f"- View: {fundamental_analysis.get('valuation', {}).get('relative_multiple_view', 'N/A')}",
            "",
            "## Risk Analysis",
        ]
    )
    for risk in fundamental_analysis.get("fundamental_risks", []):
        lines.append(f"- {risk.get('name', 'Unknown')}: {risk.get('signal', 'N/A')}")
    for risk in market_sentiment.get("sentiment_risks", []):
        lines.append(f"- {risk.get('name', 'Unknown')}: {risk.get('signal', 'N/A')}")

    lines.extend(["", "## Future Scenarios"])
    for scenario in scenarios:
        lines.append(f"- {scenario.name}: {scenario.score:.2f} — {scenario.description}")

    lines.extend(
        [
            "",
            "## Bull / Base / Bear Thesis",
            "- Bull: Execution upside with improving demand signals.",
            "- Base: Growth normalisation with stable margins.",
            "- Bear: Demand weakness and multiple compression.",
            "",
            "## What To Watch Next",
            "- Revenue growth trajectory vs long-term trend",
            "- Gross margin direction and inventory signals",
            "- External demand and regulation changes",
            "",
            "## Sources",
        ]
    )
    for item in evidence:
        lines.append(
            f"- {item['title']} | {item.get('url', 'N/A')} | "
            f"retrieved: {item.get('retrieved_at', 'N/A')}"
        )

    lines.extend(["", "## Disclaimer", "Not financial advice."])
    return "\n".join(lines)
