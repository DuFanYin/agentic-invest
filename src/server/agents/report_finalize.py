"""Report finalization node — writes report and runs final quality checks."""

from __future__ import annotations

import json
import logging

from src.server.models.analysis import (
    FundamentalAnalysis,
    MacroAnalysis,
    MarketSentiment,
    QualityMetrics,
    ScenarioDebate,
)
from src.server.models.response import ValidationResult
from src.server.models.scenario import Scenario
from src.server.models.state import ResearchState
from src.server.services.openrouter import OpenRouterClient
from src.server.utils.status import update_status
from src.server.utils.validation import (
    validate_claim_coverage,
    validate_evidence_completeness,
    validate_scenario_scores,
)

logger = logging.getLogger(__name__)

_default_llm = OpenRouterClient()
_default_llm.max_retries = 1  # report is long; limit retries to avoid timeout cascades
_NODE = "report_finalize"

_SYSTEM = (
    "You are a senior investment analyst writing a structured research report. "
    "Write clear, concise Markdown. Ground every claim in the evidence provided. "
    "Do not invent data. Return only the Markdown report — no JSON wrapper."
)

_SECTIONS = (
    "# Executive Summary",
    "## Company Overview",
    "## Key Evidence",
    "## Fundamental Analysis",
    "## Macro Environment",
    "## Market Sentiment",
    "## Valuation View",
    "## Risk Analysis",
    "## Future Scenarios",
    "## Scenario Debate & Calibration",
    "## Scenario Implications",
    "## What To Watch Next",
    "## Sources",
    "## Disclaimer",
)

_SECTION_LIST = "\n".join(f"- {s}" for s in _SECTIONS)


def _scenario_probability_sum(
    scenarios: list[Scenario],
    debate: ScenarioDebate | None,
) -> float:
    """Return probability sum, preferring calibrated debate scenarios when available."""
    if (
        isinstance(debate, ScenarioDebate)
        and debate.calibrated_scenarios
        and "fallback_to_baseline" not in debate.debate_flags
    ):
        return sum(float(s.get("probability", 0.0)) for s in debate.calibrated_scenarios)
    return sum(s.probability for s in scenarios)


def _delivery_retry_questions(errors: list[str]) -> list[str]:
    """Return retry questions for delivery-quality failures only.

    Boundary with retry gate:
    - retry_gate handles evidence adequacy.
    - report_finalize only requests retry for final delivery issues
      (e.g. unsupported claims / broken evidence citation linkage).
    """
    citation_errors = [e for e in errors if "unknown evidence" in e or "missing evidence" in e]
    return [f"report_finalize: {e}" for e in citation_errors]


def _build_prompt(intent, evidence, fundamental_analysis, macro_analysis, market_sentiment, scenarios, debate) -> str:
    subjects = ", ".join(intent.subjects) if intent and intent.subjects else "unknown"
    ticker = intent.ticker if intent else "N/A"
    horizon = intent.time_horizon if intent else "unspecified"

    ev_lines = "\n".join(
        f"[{e['id']}] ({e['source_type']}) {e['title']}: {e['summary'][:200]}"
        for e in evidence
    )

    if isinstance(fundamental_analysis, FundamentalAnalysis):
        fa_claims = "\n".join(
            f"- {c.statement} (confidence: {c.confidence}, ids: {c.evidence_ids})"
            for c in fundamental_analysis.claims
        )
        fa_risks = "\n".join(
            f"- {r.name}: {r.signal}"
            for r in fundamental_analysis.fundamental_risks
        )
        fa_bq = fundamental_analysis.business_quality.view
        fa_val = fundamental_analysis.valuation.relative_multiple_view
        metrics = fundamental_analysis.metrics
    else:
        fa_claims = fa_risks = ""
        fa_bq = fa_val = "unknown"
        metrics = {}

    if isinstance(macro_analysis, MacroAnalysis):
        macro_view = macro_analysis.macro_view
        macro_rate = macro_analysis.rate_environment
        macro_growth = macro_analysis.growth_environment
        macro_drivers = "\n".join(f"- {d}" for d in macro_analysis.macro_drivers) or "none"
        macro_risks = "\n".join(
            f"- {r.name} ({r.impact}): {r.signal}" for r in macro_analysis.macro_risks
        ) or "none"
    else:
        macro_view = macro_rate = macro_growth = "unknown"
        macro_drivers = macro_risks = "none"

    if isinstance(market_sentiment, MarketSentiment):
        ms_direction = market_sentiment.news_sentiment.direction
        ms_narrative = market_sentiment.market_narrative.summary
        ms_risks = "\n".join(
            f"- {r.name}: {r.signal}"
            for r in market_sentiment.sentiment_risks
        )
    else:
        ms_direction = "unknown"
        ms_narrative = ""
        ms_risks = ""

    # Use calibrated scenarios if debate succeeded
    if isinstance(debate, ScenarioDebate) and debate.calibrated_scenarios and "fallback_to_baseline" not in debate.debate_flags:
        sc_lines = "\n".join(
            f"- {s.get('name', '?')} ({s.get('probability', 0):.0%}) [{', '.join(s.get('tags', []))}]"
            for s in debate.calibrated_scenarios
        )
    else:
        sc_lines = "\n".join(
            f"- {s.name} ({s.probability:.0%}) [{', '.join(s.tags)}]: {s.description}"
            for s in scenarios
        )

    if isinstance(debate, ScenarioDebate):
        debate_summary = debate.debate_summary
        adj_lines = "\n".join(
            f"- {a.scenario_name}: {a.before:.0%} → {a.after:.0%} ({'+' if a.delta >= 0 else ''}{a.delta:.0%}): {a.reason}"
            for a in debate.probability_adjustments
        ) or "No adjustments made."
        debate_flags = ", ".join(debate.debate_flags) if debate.debate_flags else "none"
    else:
        debate_summary = "Debate not available."
        adj_lines = "Not available."
        debate_flags = "none"

    metrics_json = json.dumps(metrics, indent=2) if metrics else "{}"

    return f"""Write a full investment research report in Markdown. Use exactly these sections in order:
{_SECTION_LIST}

CONTEXT:
Ticker: {ticker} | Subjects: {subjects} | Horizon: {horizon}

EVIDENCE:
{ev_lines}

FINANCIAL METRICS:
{metrics_json}

FUNDAMENTAL ANALYSIS:
Business quality: {fa_bq}
Valuation: {fa_val}
Claims:
{fa_claims}
Risks:
{fa_risks}

MACRO ENVIRONMENT:
View: {macro_view}
Rate environment: {macro_rate}
Growth environment: {macro_growth}
Key drivers:
{macro_drivers}
Macro risks:
{macro_risks}

MARKET SENTIMENT:
Direction: {ms_direction}
Narrative: {ms_narrative}
Risks:
{ms_risks}

SCENARIOS (after debate calibration):
{sc_lines}

SCENARIO DEBATE:
Summary: {debate_summary}
Probability adjustments:
{adj_lines}
Debate flags: {debate_flags}

INSTRUCTIONS:
- Cite evidence IDs (e.g. [ev_001]) where relevant.
- In "## Macro Environment" describe the macro backdrop and its implications.
- In "## Scenario Debate & Calibration" summarise the debate outcome and any probability shifts.
- If debate_flags contains "fallback_to_baseline", note that debate calibration was skipped.
- The Disclaimer section must say "Not financial advice."
- Keep the report concise but substantive — 500-900 words total.
"""


async def report_finalize_node(
    state: ResearchState, *, llm: OpenRouterClient = _default_llm
) -> ResearchState:
    intent = state.get("intent")
    evidence = state.get("evidence") or []
    fundamental_analysis = state.get("fundamental_analysis")
    macro_analysis = state.get("macro_analysis")
    market_sentiment = state.get("market_sentiment")
    scenarios: list[Scenario] = state.get("scenarios") or []
    debate = state.get("scenario_debate")
    statuses = list(state.get("agent_statuses") or [])
    if statuses:
        statuses = update_status(
            statuses, "report_finalize",
            lifecycle="active", phase="generating_report", action="running validation and report generation",
        )

    evidence_dump = [item.model_dump() for item in evidence]
    available_evidence_ids = {item["id"] for item in evidence_dump}

    # validation (pure Python, always runs)
    errors: list[str] = []
    errors.extend(validate_scenario_scores(scenarios))
    errors.extend(validate_evidence_completeness(evidence_dump))
    if isinstance(fundamental_analysis, FundamentalAnalysis):
        errors.extend(validate_claim_coverage(fundamental_analysis, available_evidence_ids))
    if isinstance(market_sentiment, MarketSentiment):
        errors.extend(validate_claim_coverage(market_sentiment, available_evidence_ids))

    warnings: list[str] = []
    fa_missing = fundamental_analysis.missing_fields if isinstance(fundamental_analysis, FundamentalAnalysis) else []
    ms_missing = market_sentiment.missing_fields if isinstance(market_sentiment, MarketSentiment) else []
    if fa_missing:
        warnings.append(f"Missing fields: {', '.join(fa_missing)}")
    if ms_missing:
        warnings.append(f"Missing sentiment fields: {', '.join(ms_missing)}")

    # LLM report generation
    report_markdown: str | None = None

    if evidence:
        prompt = _build_prompt(intent, evidence_dump, fundamental_analysis, macro_analysis, market_sentiment, scenarios, debate)
        try:
            raw = await llm.complete_text(prompt, system=_SYSTEM, node=_NODE)
            content = (raw or "").strip()
            report_markdown = content if len(content) > 100 else None
        except Exception as exc:
            logger.warning("%s: LLM step failed — %s", _NODE, exc)
        if report_markdown and errors:
            report_markdown += "\n\n## Validation Errors\n" + "\n".join(
                f"- {e}" for e in errors
            )
        if report_markdown and warnings:
            report_markdown += "\n\n## Validation Warnings\n" + "\n".join(
                f"- {w}" for w in warnings
            )

    if report_markdown is None:
        msg = f"[{_NODE}] unable to generate report markdown from LLM"
        logger.error(msg)
        if statuses:
            statuses = update_status(
                statuses,
                "report_finalize",
                lifecycle="failed",
                phase="generating_report",
                action="report generation failed",
                last_error=msg,
            )
        raise RuntimeError(msg)

    # Compute quality metrics
    cited_claims = []
    if isinstance(fundamental_analysis, FundamentalAnalysis):
        cited_claims.extend(fundamental_analysis.claims)
    if isinstance(market_sentiment, MarketSentiment):
        cited_claims.extend(market_sentiment.claims)
    valid_citations = sum(
        1 for c in cited_claims
        if any(eid in available_evidence_ids for eid in c.evidence_ids)
    )
    citation_coverage = valid_citations / len(cited_claims) if cited_claims else 0.0
    prob_sum = _scenario_probability_sum(scenarios, debate)
    debate_applied = (
        isinstance(debate, ScenarioDebate)
        and len(debate.probability_adjustments) > 0
        and "fallback_to_baseline" not in debate.debate_flags
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
        scenario_probability_valid=abs(prob_sum - 1.0) < 0.01,
        debate_applied=debate_applied,
        unresolved_issues=unresolved,
        confidence=qm_confidence,
    )

    report_json = {
        "intent": intent.model_dump() if intent else {},
        "evidence": evidence_dump,
        "fundamental_analysis": fundamental_analysis.model_dump() if isinstance(fundamental_analysis, FundamentalAnalysis) else {},
        "macro_analysis": macro_analysis.model_dump() if isinstance(macro_analysis, MacroAnalysis) else {},
        "market_sentiment": market_sentiment.model_dump() if isinstance(market_sentiment, MarketSentiment) else {},
        "scenarios": [s.model_dump() for s in scenarios],
        "scenario_debate": debate.model_dump() if isinstance(debate, ScenarioDebate) else {},
        "quality_metrics": quality_metrics.model_dump(),
        "validation": {"errors": errors, "warnings": warnings},
    }

    # Report finalizer emits retry only for delivery-quality citation failures.
    retry_questions = _delivery_retry_questions(errors)
    stop_reason = "" if retry_questions else "complete"

    if statuses:
        statuses = update_status(
            statuses, "report_finalize",
            lifecycle="standby", phase="generating_report", action="report published",
            details=[
                f"is_valid={not errors}",
                f"errors={len(errors)}",
                f"retry_questions={len(retry_questions)}",
            ],
        )
        statuses = update_status(
            statuses, "parse_intent",
            lifecycle="standby", phase="workflow_complete", action="workflow complete",
        )

    return {
        "report_markdown": report_markdown,
        "report_json": report_json,
        "validation_result": ValidationResult(
            is_valid=not errors, errors=errors, warnings=warnings
        ),
        "quality_metrics": quality_metrics,
        "retry_questions": retry_questions,
        "stop_reason": stop_reason,
        "agent_statuses": statuses,
    }
