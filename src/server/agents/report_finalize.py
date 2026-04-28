"""Report finalization node — per-section narrative rendering with streaming queue."""

from __future__ import annotations

import logging

from src.server.models.analysis import (
    CustomSection,
    FundamentalAnalysis,
    MacroAnalysis,
    MarketSentiment,
    QualityMetrics,
    ReportSection,
    ScenarioDebate,
)
from src.server.models.response import ValidationResult
from src.server.models.scenario import Scenario
from src.server.models.state import ResearchState
from src.server.services.openrouter import OpenRouterClient
from src.server.services.section_queue import SectionQueue
from src.server.utils.contract import NODE_CONTRACTS, assert_writes
from src.server.utils.status import update_status

_READS  = NODE_CONTRACTS["report_finalize"].reads
_WRITES = NODE_CONTRACTS["report_finalize"].writes
from src.server.utils.validation import (
    validate_claim_coverage,
    validate_evidence_completeness,
    validate_scenario_scores,
)

logger = logging.getLogger(__name__)

_default_llm = OpenRouterClient()
_default_llm.max_retries = 1
_NODE = "report_finalize"

_SYSTEM = (
    "You are a senior investment analyst writing one section of a structured research report. "
    "Write clear, concise Markdown. Ground every claim in the evidence provided. "
    "Do not invent data. Return only the Markdown for this section — no JSON wrapper, "
    "no preamble, start directly with the section heading."
)

# Sections that are rendered from structured data — no LLM narrative needed
_STRUCTURED_SOURCES = {
    "fundamental_analysis",
    "macro_analysis",
    "market_sentiment",
    "scenarios",
    "scenario_debate",
    "evidence",
}

_FALLBACK_SECTIONS = [
    ReportSection(id="executive_summary",    title="Executive Summary",          source="all",                  required=True),
    ReportSection(id="fundamental_analysis", title="Fundamental Analysis",       source="fundamental_analysis", required=True),
    ReportSection(id="macro_environment",    title="Macro Environment",          source="macro_analysis",       required=True),
    ReportSection(id="market_sentiment",     title="Market Sentiment",           source="market_sentiment",     required=True),
    ReportSection(id="scenarios",            title="Future Scenarios",           source="scenarios",            required=True),
    ReportSection(id="scenario_debate",      title="Scenario Calibration",       source="scenario_debate",      required=True),
    ReportSection(id="conclusion",           title="Conclusion & What To Watch", source="all",                  required=True),
]


# ── Source data formatters (used in LLM prompt context only) ──────────────

def _fmt_evidence(evidence: list) -> str:
    return "\n".join(
        f"[{e['id']}] ({e['source_type']}) {e['title']}: {e['summary'][:200]}"
        for e in evidence
    ) or "No evidence available."


def _fmt_fundamental(fa: FundamentalAnalysis) -> str:
    claims = "\n".join(
        f"- {c.statement} (confidence: {c.confidence}, refs: {c.evidence_ids})"
        for c in fa.claims
    )
    risks = "\n".join(f"- {r.name} [{r.impact}]: {r.signal}" for r in fa.fundamental_risks)
    return (
        f"Business quality: {fa.business_quality.view}\n"
        f"Valuation: {fa.valuation.relative_multiple_view}\n"
        f"Key findings:\n{claims}\n"
        f"Risks:\n{risks or 'none'}"
    )


def _fmt_macro(macro: MacroAnalysis) -> str:
    drivers = "\n".join(f"- {d}" for d in macro.macro_drivers)
    risks = "\n".join(f"- {r.name} [{r.impact}]: {r.signal}" for r in macro.macro_risks)
    return (
        f"View: {macro.macro_view}\n"
        f"Rate environment: {macro.rate_environment}\n"
        f"Growth environment: {macro.growth_environment}\n"
        f"Drivers:\n{drivers or 'none'}\n"
        f"Risks:\n{risks or 'none'}"
    )


def _fmt_sentiment(ms: MarketSentiment) -> str:
    claims = "\n".join(
        f"- {c.statement} (confidence: {c.confidence})" for c in ms.claims
    )
    risks = "\n".join(f"- {r.name} [{r.impact}]: {r.signal}" for r in ms.sentiment_risks)
    pa = ms.price_action
    price_str = f"30d return: {pa.return_30d_pct}% | volatility: {pa.volatility}\n" if pa else ""
    return (
        f"News direction: {ms.news_sentiment.direction}\n"
        f"{price_str}"
        f"Narrative: {ms.market_narrative.summary}\n"
        f"Key findings:\n{claims or 'none'}\n"
        f"Risks:\n{risks or 'none'}"
    )


def _fmt_scenarios(scenarios: list[Scenario], debate: ScenarioDebate | None) -> str:
    calibrated: dict[str, float] = {}
    if (
        isinstance(debate, ScenarioDebate)
        and debate.calibrated_scenarios
        and "fallback_to_baseline" not in debate.debate_flags
    ):
        for s in debate.calibrated_scenarios:
            if s.get("name"):
                calibrated[s["name"]] = s.get("probability", 0.0)

    lines = []
    for s in scenarios:
        prob = calibrated.get(s.name, s.probability)
        lines.append(f"- {s.name} ({prob:.0%}): {s.description[:120]}")
        if s.drivers:
            lines.append(f"  Drivers: {', '.join(s.drivers)}")
    return "\n".join(lines) or "No scenarios available."


def _fmt_debate(debate: ScenarioDebate) -> str:
    parts = [f"Summary: {debate.debate_summary}", f"Confidence: {debate.confidence}"]
    for a in debate.probability_adjustments:
        sign = "+" if a.delta >= 0 else ""
        parts.append(f"- {a.scenario_name}: {a.before:.0%} → {a.after:.0%} ({sign}{a.delta:.0%}): {a.reason}")
    return "\n".join(parts)


def _all_context(intent, evidence_dump, fa, macro, ms, scenarios, debate, metrics) -> str:
    parts = []
    if intent:
        parts.append(f"Ticker: {intent.ticker or 'N/A'} | Horizon: {intent.time_horizon or 'unspecified'}")
    if evidence_dump:
        parts.append(f"EVIDENCE:\n{_fmt_evidence(evidence_dump[:8])}")
    if isinstance(fa, FundamentalAnalysis):
        parts.append(f"FUNDAMENTALS:\n{_fmt_fundamental(fa)}")
    if isinstance(macro, MacroAnalysis):
        parts.append(f"MACRO:\n{_fmt_macro(macro)}")
    if isinstance(ms, MarketSentiment):
        parts.append(f"SENTIMENT:\n{_fmt_sentiment(ms)}")
    if scenarios:
        parts.append(f"SCENARIOS:\n{_fmt_scenarios(scenarios, debate)}")
    if isinstance(debate, ScenarioDebate) and "fallback_to_baseline" not in debate.debate_flags:
        parts.append(f"DEBATE:\n{_fmt_debate(debate)}")
    return "\n\n".join(parts)


def _section_context(
    section: ReportSection,
    evidence_dump, fa, macro, ms, scenarios, debate, intent, metrics,
) -> str:
    src = section.source
    if src == "fundamental_analysis" and isinstance(fa, FundamentalAnalysis):
        return _fmt_fundamental(fa)
    if src == "macro_analysis" and isinstance(macro, MacroAnalysis):
        return _fmt_macro(macro)
    if src == "market_sentiment" and isinstance(ms, MarketSentiment):
        return _fmt_sentiment(ms)
    if src == "scenarios":
        return _fmt_scenarios(scenarios, debate)
    if src == "scenario_debate" and isinstance(debate, ScenarioDebate):
        return _fmt_debate(debate)
    if src == "evidence":
        return _fmt_evidence(evidence_dump)
    return _all_context(intent, evidence_dump, fa, macro, ms, scenarios, debate, metrics)


# ── Narrative section rendering ────────────────────────────────────────────

async def _render_narrative(
    section: ReportSection,
    context: str,
    llm: OpenRouterClient,
) -> str:
    prompt = (
        f"Write the '{section.title}' section of an investment research report.\n\n"
        f"DATA FOR THIS SECTION:\n{context}\n\n"
        f"Instructions:\n"
        f"- Start with '## {section.title}' as the heading.\n"
        f"- Ground claims in the data above — cite evidence IDs like [ev_001] where relevant.\n"
        f"- 80-150 words. Concise and substantive.\n"
        f"- Not financial advice."
    )
    try:
        raw = await llm.complete_text(prompt, system=_SYSTEM, node=_NODE)
        content = (raw or "").strip()
        if len(content) > 50:
            return content
    except Exception as exc:
        logger.warning("%s: narrative for '%s' failed — %s", _NODE, section.id, exc)
    return f"## {section.title}\n\n*Section unavailable.*"


# ── Node entry point ────────────────────────────────────────────────────────

async def report_finalize_node(
    state: ResearchState,
    *,
    llm: OpenRouterClient = _default_llm,
    section_queue: SectionQueue | None = None,
) -> ResearchState:

    intent = state.get("intent")
    evidence = state.get("evidence") or []
    fa = state.get("fundamental_analysis")
    macro = state.get("macro_analysis")
    ms = state.get("market_sentiment")
    scenarios: list[Scenario] = state.get("scenarios") or []
    debate = state.get("scenario_debate")
    plan_ctx = state.get("plan_context")
    report_plan = plan_ctx.report_plan if plan_ctx else None
    custom_sections: list[CustomSection] = plan_ctx.custom_sections if plan_ctx else []
    statuses = list(state.get("agent_statuses") or [])

    if statuses:
        statuses = update_status(
            statuses, "report_finalize",
            lifecycle="active", phase="generating_report", action="rendering sections",
        )

    evidence_dump = [item.model_dump() for item in evidence]
    available_evidence_ids = {item["id"] for item in evidence_dump}
    metrics = fa.metrics if isinstance(fa, FundamentalAnalysis) else {}

    if not evidence:
        msg = f"[{_NODE}] no evidence — cannot generate report"
        logger.error(msg)
        raise RuntimeError(msg)

    # Validation
    errors: list[str] = []
    errors.extend(validate_scenario_scores(scenarios))
    errors.extend(validate_evidence_completeness(evidence_dump))
    if isinstance(fa, FundamentalAnalysis):
        errors.extend(validate_claim_coverage(fa, available_evidence_ids))
    if isinstance(ms, MarketSentiment):
        errors.extend(validate_claim_coverage(ms, available_evidence_ids))

    warnings: list[str] = []
    if isinstance(fa, FundamentalAnalysis) and fa.missing_fields:
        warnings.append(f"Missing fields: {', '.join(fa.missing_fields)}")
    if isinstance(ms, MarketSentiment) and ms.missing_fields:
        warnings.append(f"Missing sentiment fields: {', '.join(ms.missing_fields)}")

    sections = report_plan.sections if report_plan else _FALLBACK_SECTIONS

    # Render sections — narrative ones via LLM, structured ones signalled directly
    narrative_sections: dict[str, str] = {}
    report_parts: list[str] = []
    uncovered: list[str] = []

    for section in sections:
        src = section.source

        if src in _STRUCTURED_SOURCES:
            # Structured section — frontend renders from typed data, just signal it's ready
            if section_queue:
                section_queue.push(section.id, "", src)
            continue

        # Narrative section — LLM writes it
        context = _section_context(section, evidence_dump, fa, macro, ms, scenarios, debate, intent, metrics)
        content = await _render_narrative(section, context, llm)
        narrative_sections[section.id] = content
        report_parts.append(content)

        if section_queue:
            section_queue.push(section.id, content, src)

        if statuses:
            statuses = update_status(
                statuses, "report_finalize",
                lifecycle="active", phase="generating_report",
                action=f"section ready: {section.title}",
            )

    # Custom sections — query-specific narratives proposed by planning agent
    full_ctx = _all_context(intent, evidence_dump, fa, macro, ms, scenarios, debate, metrics)
    for cs in custom_sections:
        synthetic = ReportSection(id=cs.id, title=cs.title, source="all", required=False)
        # Prepend the focus directive so the LLM answers the specific question
        focused_ctx = f"FOCUS FOR THIS SECTION: {cs.focus}\n\n{full_ctx}"
        content = await _render_narrative(synthetic, focused_ctx, llm)
        narrative_sections[cs.id] = content
        report_parts.append(content)
        if section_queue:
            section_queue.push(cs.id, content, "custom", title=cs.title)
        if statuses:
            statuses = update_status(
                statuses, "report_finalize",
                lifecycle="active", phase="generating_report",
                action=f"section ready: {cs.title}",
            )

    if section_queue:
        section_queue.done()

    # Assemble export markdown (structured sections summarised in Python)
    export_parts = list(report_parts)
    if isinstance(fa, FundamentalAnalysis):
        export_parts.append(f"## Fundamental Analysis\n\n{_fmt_fundamental(fa)}")
    if isinstance(macro, MacroAnalysis):
        export_parts.append(f"## Macro Environment\n\n{_fmt_macro(macro)}")
    if isinstance(ms, MarketSentiment):
        export_parts.append(f"## Market Sentiment\n\n{_fmt_sentiment(ms)}")
    if scenarios:
        export_parts.append(f"## Future Scenarios\n\n{_fmt_scenarios(scenarios, debate)}")
    if isinstance(debate, ScenarioDebate):
        export_parts.append(f"## Scenario Calibration\n\n{_fmt_debate(debate)}")
    if errors:
        export_parts.append("## Validation Errors\n" + "\n".join(f"- {e}" for e in errors))
    if warnings:
        export_parts.append("## Validation Warnings\n" + "\n".join(f"- {w}" for w in warnings))
    export_parts.append("---\n*Not financial advice.*")
    report_markdown = "\n\n".join(export_parts)

    # Quality metrics
    cited_claims = []
    if isinstance(fa, FundamentalAnalysis):
        cited_claims.extend(fa.claims)
    if isinstance(ms, MarketSentiment):
        cited_claims.extend(ms.claims)
    valid_citations = sum(
        1 for c in cited_claims if any(eid in available_evidence_ids for eid in c.evidence_ids)
    )
    citation_coverage = valid_citations / len(cited_claims) if cited_claims else 0.0

    if (
        isinstance(debate, ScenarioDebate)
        and debate.calibrated_scenarios
        and "fallback_to_baseline" not in debate.debate_flags
    ):
        prob_sum = sum(float(s.get("probability", 0.0)) for s in debate.calibrated_scenarios)
    else:
        prob_sum = sum(s.probability for s in scenarios)

    debate_applied = (
        isinstance(debate, ScenarioDebate)
        and len(debate.probability_adjustments) > 0
        and "fallback_to_baseline" not in debate.debate_flags
    )
    unresolved = len(errors) + len(uncovered)
    if unresolved == 0 and citation_coverage >= 0.8:
        qm_confidence = "high"
    elif unresolved <= 2:
        qm_confidence = "medium"
    else:
        qm_confidence = "low"

    quality_metrics = QualityMetrics(
        citation_coverage=round(citation_coverage, 4),
        scenario_probability_valid=abs(prob_sum - 1.0) < 0.01,
        debate_applied=debate_applied,
        unresolved_issues=unresolved,
        confidence=qm_confidence,
    )

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
        "validation": {"errors": errors, "warnings": warnings, "uncovered_sections": uncovered},
    }

    citation_errors = [e for e in errors if "unknown evidence" in e or "missing evidence" in e]
    retry_questions = [f"report_finalize: {e}" for e in citation_errors]
    stop_reason = "" if retry_questions else "complete"

    if statuses:
        statuses = update_status(
            statuses, "report_finalize",
            lifecycle="standby", phase="generating_report", action="report published",
            details=[f"narrative={len(narrative_sections)}", f"errors={len(errors)}"],
        )
        statuses = update_status(
            statuses, "parse_intent",
            lifecycle="standby", phase="workflow_complete", action="workflow complete",
        )

    delta = {
        "narrative_sections": narrative_sections,
        "report_markdown": report_markdown,
        "report_json": report_json,
        "validation_result": ValidationResult(is_valid=not errors, errors=errors, warnings=warnings),
        "quality_metrics": quality_metrics,
        "retry_questions": retry_questions,
        "stop_reason": stop_reason,
        "agent_statuses": statuses,
    }
    assert_writes(delta, _WRITES, "report_finalize")
    return delta
