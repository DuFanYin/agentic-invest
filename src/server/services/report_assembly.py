"""Report assembly — pure synchronous functions for validation, quality metrics, and JSON/Markdown assembly.

No LLM calls. Takes already-rendered narrative sections and structured analysis objects,
returns a complete AssemblyResult ready for the node to write to state.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.server.models.analysis import (
    FundamentalAnalysis,
    MacroAnalysis,
    MarketSentiment,
    QualityMetrics,
    ReportPlan,
    ReportSection,
    ScenarioDebate,
)
from src.server.models.response import ValidationResult
from src.server.models.scenario import Scenario
from src.server.utils.validation import (
    SCENARIO_PROB_TOLERANCE,
    validate_claim_coverage,
    validate_evidence_completeness,
    validate_scenario_scores,
)

# ── Plan validation constants ──────────────────────────────────────────────

_CANONICAL_SECTION_IDS = {
    "executive_summary",
    "fundamental_analysis",
    "macro_environment",
    "market_sentiment",
    "scenarios",
    "scenario_debate",
    "conclusion",
}

_FALLBACK_SECTIONS = [
    ReportSection(id="executive_summary", title="Executive Summary", source="all", required=True),
    ReportSection(
        id="fundamental_analysis", title="Fundamental Analysis", source="fundamental_analysis", required=True
    ),
    ReportSection(id="macro_environment", title="Macro Environment", source="macro_analysis", required=True),
    ReportSection(id="market_sentiment", title="Market Sentiment", source="market_sentiment", required=True),
    ReportSection(id="scenarios", title="Future Scenarios", source="scenarios", required=True),
    ReportSection(id="scenario_debate", title="Scenario Calibration", source="scenario_debate", required=True),
    ReportSection(id="conclusion", title="Conclusion & What To Watch", source="all", required=True),
]
_FALLBACK_SECTION_BY_ID = {s.id: s for s in _FALLBACK_SECTIONS}


# ── Public result type ─────────────────────────────────────────────────────


@dataclass
class AssemblyResult:
    errors: list[str]
    warnings: list[str]
    quality_metrics: QualityMetrics
    validation_result: ValidationResult
    report_json: dict
    report_markdown: str


# ── Plan validation ────────────────────────────────────────────────────────


def validate_report_plan(report_plan: ReportPlan | None) -> tuple[list[ReportSection], list[str], ReportPlan]:
    """Return (usable_sections, warnings, accepted_plan).

    Prefers salvaging valid sections over falling back the entire plan.
    """
    if report_plan is None:
        fallback = ReportPlan(report_type="general", sections=list(_FALLBACK_SECTIONS))
        return list(_FALLBACK_SECTIONS), [], fallback

    warnings: list[str] = []
    validated: list[ReportSection] = []
    seen_ids: set[str] = set()

    for section in report_plan.sections:
        section_id = (section.id or "").strip()
        source = (section.source or "").strip()
        title = (section.title or "").strip()
        if section_id not in _CANONICAL_SECTION_IDS:
            warnings.append(f"Skipping unknown report_plan section id '{section_id}'")
            continue
        if section_id in seen_ids:
            warnings.append(f"Skipping duplicate report_plan section id '{section_id}'")
            continue

        canonical = _FALLBACK_SECTION_BY_ID[section_id]
        if source != canonical.source:
            prev = source or "<empty>"
            warnings.append(
                f"Normalizing report_plan source for section '{section_id}' from '{prev}' to '{canonical.source}'"
            )
            source = canonical.source
        if not title:
            warnings.append(f"Using default title for report_plan section '{section_id}'")
            title = canonical.title

        seen_ids.add(section_id)
        validated.append(ReportSection(id=section_id, title=title, source=source, required=section.required))

    if not validated:
        warnings.append("No usable report_plan sections — using fallback plan")
        fallback = ReportPlan(report_type=report_plan.report_type or "general", sections=list(_FALLBACK_SECTIONS))
        return list(_FALLBACK_SECTIONS), warnings, fallback

    return (validated, warnings, ReportPlan(report_type=report_plan.report_type, sections=validated))


# ── Assembly entry point ───────────────────────────────────────────────────


def assemble(
    *,
    intent,
    evidence_dump: list[dict],
    fa,
    macro,
    ms,
    scenarios: list[Scenario],
    debate,
    report_plan: ReportPlan,
    custom_sections: list,
    narrative_sections: dict[str, str],
    report_parts: list[str],
    retry_reason: str,
    fmt_fundamental,
    fmt_macro,
    fmt_sentiment,
    fmt_scenarios,
    fmt_debate,
) -> AssemblyResult:
    """Build errors, warnings, quality metrics, report_json, and report_markdown.

    fmt_* are formatter callables passed in from report_finalize to avoid duplicating
    the serialization logic — they live with the LLM rendering code that also uses them.
    """
    available_evidence_ids = {item["id"] for item in evidence_dump}

    # Validation errors
    errors: list[str] = []
    errors.extend(validate_scenario_scores(scenarios))
    errors.extend(validate_evidence_completeness(evidence_dump))
    if isinstance(fa, FundamentalAnalysis):
        errors.extend(validate_claim_coverage(fa, available_evidence_ids))
    if isinstance(ms, MarketSentiment):
        errors.extend(validate_claim_coverage(ms, available_evidence_ids))

    # Warnings
    warnings: list[str] = []
    if isinstance(fa, FundamentalAnalysis) and fa.degraded:
        warnings.append("fundamental_analysis unavailable: LLM exhausted")
    if isinstance(macro, MacroAnalysis) and macro.degraded:
        warnings.append("macro_analysis unavailable: LLM exhausted")
    if isinstance(ms, MarketSentiment) and ms.degraded:
        warnings.append("market_sentiment unavailable: LLM exhausted")
    if isinstance(debate, ScenarioDebate) and debate.degraded:
        warnings.append("scenario_debate unavailable: baseline probabilities used")
    if retry_reason == "judge_degraded":
        warnings.append("llm_judge unavailable: retry decision skipped")

    # Quality metrics
    cited_claims = []
    if isinstance(fa, FundamentalAnalysis):
        cited_claims.extend(fa.claims)
    if isinstance(ms, MarketSentiment):
        cited_claims.extend(ms.claims)
    valid_citations = sum(1 for c in cited_claims if any(eid in available_evidence_ids for eid in c.evidence_ids))
    citation_coverage = valid_citations / len(cited_claims) if cited_claims else 0.0

    if isinstance(debate, ScenarioDebate) and debate.calibrated_scenarios and not debate.degraded:
        prob_sum = sum(float(row.probability) for row in debate.calibrated_scenarios)
    else:
        prob_sum = sum(s.probability for s in scenarios)

    debate_applied = (
        isinstance(debate, ScenarioDebate) and len(debate.probability_adjustments) > 0 and not debate.degraded
    )
    unresolved = len(errors)
    if unresolved == 0 and citation_coverage >= 0.8:
        qm_confidence = "high"
    elif unresolved <= 2:
        qm_confidence = "medium"
    else:
        qm_confidence = "low"

    quality_metrics = QualityMetrics(
        citation_coverage=round(citation_coverage, 4),
        scenario_probability_valid=abs(prob_sum - 1.0) <= SCENARIO_PROB_TOLERANCE,
        debate_applied=debate_applied,
        unresolved_issues=unresolved,
        confidence=qm_confidence,
    )

    # Markdown export (narrative + structured summaries)
    export_parts = list(report_parts)
    if isinstance(fa, FundamentalAnalysis):
        export_parts.append(f"## Fundamental Analysis\n\n{fmt_fundamental(fa)}")
    if isinstance(macro, MacroAnalysis):
        export_parts.append(f"## Macro Environment\n\n{fmt_macro(macro)}")
    if isinstance(ms, MarketSentiment):
        export_parts.append(f"## Market Sentiment\n\n{fmt_sentiment(ms)}")
    if scenarios:
        export_parts.append(f"## Future Scenarios\n\n{fmt_scenarios(scenarios, debate)}")
    if isinstance(debate, ScenarioDebate):
        export_parts.append(f"## Scenario Calibration\n\n{fmt_debate(debate)}")
    if errors:
        export_parts.append("## Validation Errors\n" + "\n".join(f"- {e}" for e in errors))
    if warnings:
        export_parts.append("## Validation Warnings\n" + "\n".join(f"- {w}" for w in warnings))
    export_parts.append("---\n*Not financial advice.*")
    report_markdown = "\n\n".join(export_parts)

    # JSON payload
    report_json = {
        "intent": intent.model_dump() if intent else {},
        "report_plan": report_plan.model_dump() if report_plan else {},
        "custom_sections": [cs.model_dump() for cs in custom_sections],
        "narrative_sections": narrative_sections,
        "evidence": evidence_dump,
        "fundamental_analysis": fa.model_dump() if isinstance(fa, FundamentalAnalysis) else {},
        "macro_analysis": macro.model_dump() if isinstance(macro, MacroAnalysis) else {},
        "market_sentiment": ms.model_dump() if isinstance(ms, MarketSentiment) else {},
        "scenarios": [s.model_dump() for s in scenarios],
        "scenario_debate": debate.model_dump() if isinstance(debate, ScenarioDebate) else {},
        "quality_metrics": quality_metrics.model_dump(),
        "validation": {"errors": errors, "warnings": warnings},
    }

    return AssemblyResult(
        errors=errors,
        warnings=warnings,
        quality_metrics=quality_metrics,
        validation_result=ValidationResult(is_valid=not errors, errors=errors, warnings=warnings),
        report_json=report_json,
        report_markdown=report_markdown,
    )
