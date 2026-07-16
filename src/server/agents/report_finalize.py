"""Report finalization node — per-section narrative rendering and assembly."""

from __future__ import annotations

import asyncio
import logging

from src.server.models.analysis import (
    CustomSection,
    FundamentalAnalysis,
    MacroAnalysis,
    MarketSentiment,
    ReportSection,
    ScenarioDebate,
)
from src.server.models.scenario import Scenario
from src.server.models.state import ResearchState
from src.server.prompts import build_prompt, narrative_section_format_instructions
from src.server.services.llm_provider import LLMClient
from src.server.services.report_assembly import assemble, validate_report_plan
from src.server.utils.contract import NODE_CONTRACTS, assert_reads, assert_writes
from src.server.utils.status import update_status

logger = logging.getLogger(__name__)

_NODE = "report_finalize"

_READS = NODE_CONTRACTS["report_finalize"].reads
_WRITES = NODE_CONTRACTS["report_finalize"].writes
_MIN_SECTION_LENGTH = 50

# Sections rendered from structured data — no LLM narrative needed
_STRUCTURED_SOURCES = {
    "fundamental_analysis",
    "macro_analysis",
    "market_sentiment",
    "scenarios",
    "scenario_debate",
    "evidence",
}


# ── Source data formatters (prompt context only) ───────────────────────────


def _fmt_evidence(evidence: list) -> str:
    return (
        "\n".join(f"[{e['id']}] ({e['source_type']}) {e['title']}: {e['summary'][:200]}" for e in evidence)
        or "No evidence available."
    )


def _fmt_fundamental(fa: FundamentalAnalysis) -> str:
    claims = "\n".join(f"- {c.statement} (confidence: {c.confidence}, refs: {c.evidence_ids})" for c in fa.claims)
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
    claims = "\n".join(f"- {c.statement} (confidence: {c.confidence})" for c in ms.claims)
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
    if isinstance(debate, ScenarioDebate) and debate.calibrated_scenarios and not debate.degraded:
        for row in debate.calibrated_scenarios:
            if row.name:
                calibrated[row.name] = row.probability

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
    if isinstance(debate, ScenarioDebate) and not debate.degraded:
        parts.append(f"DEBATE:\n{_fmt_debate(debate)}")
    return "\n\n".join(parts)


def _section_context(section: ReportSection, evidence_dump, fa, macro, ms, scenarios, debate, intent, metrics) -> str:
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


async def _render_narrative(section: ReportSection, context: str, llm: LLMClient) -> str:
    fmt = narrative_section_format_instructions(section.id)
    system, prompt = build_prompt(
        "report_finalize", "narrative_section", section_title=section.title, format_instructions=fmt, context=context
    )
    try:
        raw = await llm.complete_text(prompt, system=system, node=_NODE)
        content = (raw or "").strip()
        if len(content) > _MIN_SECTION_LENGTH:
            return content
    except Exception as exc:
        logger.warning("%s: narrative for '%s' failed — %s", _NODE, section.id, exc)
    return (
        f"*Section unavailable* — narrative generation did not return enough text for “{section.title}”.\n\n"
        "*Not financial advice.*"
    )


# ── Node entry point ───────────────────────────────────────────────────────


async def report_finalize_node(state: ResearchState, *, llm: LLMClient | None = None) -> ResearchState:
    assert_reads(state, _READS, _NODE)
    if llm is None:
        llm = LLMClient()
        llm.max_retries = 1

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
            statuses, "report_finalize", lifecycle="active", phase="generating_report", action="rendering sections"
        )

    if not evidence:
        msg = f"[{_NODE}] no evidence — cannot generate report"
        logger.error(msg)
        raise RuntimeError(msg)

    fa_degraded = isinstance(fa, FundamentalAnalysis) and fa.degraded
    macro_degraded = isinstance(macro, MacroAnalysis) and macro.degraded
    ms_degraded = isinstance(ms, MarketSentiment) and ms.degraded
    if fa_degraded and macro_degraded and ms_degraded:
        raise RuntimeError(f"[{_NODE}] all three analysis nodes degraded — cannot generate report")

    evidence_dump = [item.model_dump() for item in evidence]
    metrics = fa.metrics if isinstance(fa, FundamentalAnalysis) else {}
    sections, plan_warnings, report_plan = validate_report_plan(report_plan)

    sections_main = [s for s in sections if s.id != "conclusion"]
    sections_tail = [s for s in sections if s.id == "conclusion"]

    narrative_sections: dict[str, str] = {}
    report_parts: list[str] = []

    # Build the render plan in final assembly order (main → custom → tail). Sections
    # are independent — each renders from state data, not from other sections' text —
    # so we render them concurrently instead of sequentially (was the 180s bottleneck).
    full_ctx = _all_context(intent, evidence_dump, fa, macro, ms, scenarios, debate, metrics)
    render_jobs: list[tuple[ReportSection, str]] = []

    for section in sections_main:
        if section.source in _STRUCTURED_SOURCES:
            continue
        ctx = _section_context(section, evidence_dump, fa, macro, ms, scenarios, debate, intent, metrics)
        render_jobs.append((section, ctx))

    for cs in custom_sections:
        synthetic = ReportSection(id=cs.id, title=cs.title, source="all", required=False)
        render_jobs.append((synthetic, f"FOCUS FOR THIS SECTION: {cs.focus}\n\n{full_ctx}"))

    for section in sections_tail:
        if section.source in _STRUCTURED_SOURCES:
            continue
        ctx = _section_context(section, evidence_dump, fa, macro, ms, scenarios, debate, intent, metrics)
        render_jobs.append((section, ctx))

    contents = await asyncio.gather(*[_render_narrative(section, ctx, llm) for section, ctx in render_jobs])
    for (section, _), content in zip(render_jobs, contents):
        narrative_sections[section.id] = content
        report_parts.append(content)

    if statuses and render_jobs:
        statuses = update_status(
            statuses,
            "report_finalize",
            lifecycle="active",
            phase="generating_report",
            action=f"rendered {len(render_jobs)} sections",
        )

    result = assemble(
        intent=intent,
        evidence_dump=evidence_dump,
        fa=fa,
        macro=macro,
        ms=ms,
        scenarios=scenarios,
        debate=debate,
        report_plan=report_plan,
        custom_sections=custom_sections,
        narrative_sections=narrative_sections,
        report_parts=report_parts,
        retry_reason=state.get("retry_reason", "none"),
        fmt_fundamental=_fmt_fundamental,
        fmt_macro=_fmt_macro,
        fmt_sentiment=_fmt_sentiment,
        fmt_scenarios=_fmt_scenarios,
        fmt_debate=_fmt_debate,
    )
    result.warnings[:0] = plan_warnings  # prepend plan warnings before assembly warnings

    if statuses:
        statuses = update_status(
            statuses,
            "report_finalize",
            lifecycle="standby",
            phase="generating_report",
            action="report published",
            details=[f"narrative={len(narrative_sections)}", f"errors={len(result.errors)}"],
        )
        statuses = update_status(
            statuses, "planner", lifecycle="standby", phase="workflow_complete", action="workflow complete"
        )

    delta = {
        "narrative_sections": narrative_sections,
        "report_markdown": result.report_markdown,
        "report_json": result.report_json,
        "validation_result": result.validation_result,
        "quality_metrics": result.quality_metrics,
        "retry_questions": [],
        "agent_statuses": statuses,
    }
    assert_writes(delta, _WRITES, "report_finalize")
    return delta
