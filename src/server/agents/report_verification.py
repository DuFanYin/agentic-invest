from src.server.agents.research import ResearchResult
from src.server.models.intent import ResearchIntent
from src.server.models.response import AgentStatus, ResearchResponse, ValidationResult
from src.server.models.scenario import Scenario
from src.server.utils.validation import (
    validate_claim_coverage,
    validate_evidence_completeness,
    validate_scenario_scores,
)


class ReportVerificationAgent:
    def run(
        self,
        intent: ResearchIntent,
        research_result: ResearchResult,
        fundamental_analysis: dict,
        market_sentiment: dict,
        scenarios: list[Scenario],
        agent_statuses: list[dict] | None = None,
    ) -> ResearchResponse:
        evidence_dump = [item.model_dump() for item in research_result.evidence]
        available_evidence_ids = {item["id"] for item in evidence_dump}

        errors = []
        errors.extend(validate_scenario_scores(scenarios))
        errors.extend(validate_evidence_completeness(evidence_dump))
        errors.extend(validate_claim_coverage(fundamental_analysis, available_evidence_ids))
        errors.extend(validate_claim_coverage(market_sentiment, available_evidence_ids))

        warnings = []
        if fundamental_analysis.get("missing_fields"):
            warnings.append(f"Missing fields reported: {', '.join(fundamental_analysis['missing_fields'])}")
        if market_sentiment.get("missing_fields"):
            warnings.append(f"Missing sentiment fields reported: {', '.join(market_sentiment['missing_fields'])}")

        report_markdown = self._build_markdown_report(
            intent=intent,
            evidence=evidence_dump,
            fundamental_analysis=fundamental_analysis,
            market_sentiment=market_sentiment,
            scenarios=scenarios,
        )

        report_json = {
            "intent": intent.model_dump(),
            "evidence": evidence_dump,
            "fundamental_analysis": fundamental_analysis,
            "market_sentiment": market_sentiment,
            "scenarios": [s.model_dump() for s in scenarios],
            "validation": {"errors": errors, "warnings": warnings},
        }

        return ResearchResponse(
            report_markdown=report_markdown,
            report_json=report_json,
            intent=intent,
            evidence=research_result.evidence,
            fundamental_analysis=fundamental_analysis,
            market_sentiment=market_sentiment,
            scenarios=scenarios,
            agent_statuses=[AgentStatus(**status) for status in (agent_statuses or [])],
            validation_result=ValidationResult(is_valid=not errors, errors=errors, warnings=warnings),
        )

    def _build_markdown_report(
        self,
        intent: ResearchIntent,
        evidence: list[dict],
        fundamental_analysis: dict,
        market_sentiment: dict,
        scenarios: list[Scenario],
    ) -> str:
        lines: list[str] = [
            "# Executive Summary",
            f"- Intent: {intent.intent}",
            f"- Scope: {intent.scope}",
            f"- Subjects: {', '.join(intent.subjects) if intent.subjects else 'N/A'}",
            "",
            "## Company / Theme Overview",
            "Dummy workflow output with realistic structured flow across all five agents.",
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
            lines.append(f"- {scenario.name}: {scenario.score:.2f} - {scenario.description}")

        lines.extend(
            [
                "",
                "## Bull / Base / Bear Thesis",
                "- Bull: Execution upside with improving demand signals.",
                "- Base: Growth normalization with stable margins.",
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
            lines.append(f"- {item['title']} | {item.get('url', 'N/A')} | retrieved: {item.get('retrieved_at', 'N/A')}")

        lines.extend(["", "## Disclaimer", "- 非投资建议"])
        return "\n".join(lines)
