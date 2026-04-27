"""Report generation and verification node — LLM writes the Markdown report."""

from __future__ import annotations

import json
import logging

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

_llm = OpenRouterClient()
_llm.max_retries = 1  # report is long; limit retries to avoid timeout cascades

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
    "## Market Sentiment",
    "## Valuation View",
    "## Risk Analysis",
    "## Future Scenarios",
    "## Bull / Base / Bear Thesis",
    "## What To Watch Next",
    "## Sources",
    "## Disclaimer",
)

_SECTION_LIST = "\n".join(f"- {s}" for s in _SECTIONS)


def _build_prompt(intent, evidence, fundamental_analysis, market_sentiment, scenarios) -> str:
    subjects = ", ".join(intent.subjects) if intent and intent.subjects else "unknown"
    ticker = intent.ticker if intent else "N/A"
    horizon = intent.time_horizon if intent else "unspecified"

    ev_lines = "\n".join(
        f"[{e['id']}] ({e['source_type']}) {e['title']}: {e['summary'][:200]}"
        for e in evidence
    )

    fa_claims = "\n".join(
        f"- {c['statement']} (confidence: {c.get('confidence','?')}, ids: {c.get('evidence_ids',[])})"
        for c in fundamental_analysis.get("claims", [])
    )
    fa_risks = "\n".join(
        f"- {r['name']}: {r.get('signal','')}"
        for r in fundamental_analysis.get("fundamental_risks", [])
    )
    fa_bq = fundamental_analysis.get("business_quality", {}).get("view", "unknown")
    fa_val = fundamental_analysis.get("valuation", {}).get("relative_multiple_view", "unknown")

    ms_direction = market_sentiment.get("news_sentiment", {}).get("direction", "unknown")
    ms_narrative = market_sentiment.get("market_narrative", {}).get("summary", "")
    ms_risks = "\n".join(
        f"- {r['name']}: {r.get('signal','')}"
        for r in market_sentiment.get("sentiment_risks", [])
    )

    sc_lines = "\n".join(
        f"- {s.name} ({s.score:.0%}): {s.description}"
        for s in scenarios
    )

    metrics = fundamental_analysis.get("metrics", {})
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

MARKET SENTIMENT:
Direction: {ms_direction}
Narrative: {ms_narrative}
Risks:
{ms_risks}

SCENARIOS:
{sc_lines}

INSTRUCTIONS:
- Cite evidence IDs (e.g. [ev_001]) where relevant.
- The Disclaimer section must say "Not financial advice."
- Keep the report concise but substantive — 400-800 words total.
"""


def _fallback_report(intent, evidence, fundamental_analysis, market_sentiment, scenarios) -> str:
    subjects = ", ".join(intent.subjects) if intent and intent.subjects else "N/A"
    lines = [
        "# Executive Summary",
        f"Research on {subjects} ({intent.ticker if intent else 'N/A'}).",
        "",
        "## Company Overview",
        fundamental_analysis.get("business_quality", {}).get("view", "See evidence below."),
        "",
        "## Key Evidence",
    ]
    for e in evidence:
        lines.append(f"- [{e['id']}] {e['title']}")
    lines += [
        "",
        "## Fundamental Analysis",
        f"Business quality: {fundamental_analysis.get('business_quality', {}).get('view', 'N/A')}",
        f"Valuation: {fundamental_analysis.get('valuation', {}).get('relative_multiple_view', 'N/A')}",
        "",
        "## Market Sentiment",
        f"Direction: {market_sentiment.get('news_sentiment', {}).get('direction', 'N/A')}",
        "",
        "## Valuation View",
        fundamental_analysis.get("valuation", {}).get("relative_multiple_view", "N/A"),
        "",
        "## Risk Analysis",
    ]
    for r in fundamental_analysis.get("fundamental_risks", []):
        lines.append(f"- {r.get('name')}: {r.get('signal','')}")
    for r in market_sentiment.get("sentiment_risks", []):
        lines.append(f"- {r.get('name')}: {r.get('signal','')}")
    lines += ["", "## Future Scenarios"]
    for s in scenarios:
        lines.append(f"- {s.name} ({s.score:.0%}): {s.description}")
    lines += [
        "",
        "## Bull / Base / Bear Thesis",
        "- Bull: Execution upside with improving demand signals.",
        "- Base: Growth normalisation with stable margins.",
        "- Bear: Demand weakness and multiple compression.",
        "",
        "## What To Watch Next",
        "- Revenue growth trajectory",
        "- Gross margin direction",
        "- External demand signals",
        "",
        "## Sources",
    ]
    for e in evidence:
        lines.append(f"- {e['title']} | {e.get('url','N/A')}")
    lines += ["", "## Disclaimer", "Not financial advice."]
    return "\n".join(lines)


def report_verification_node(state: ResearchState) -> ResearchState:
    intent = state.get("intent")
    evidence = state.get("evidence") or []
    fundamental_analysis = state.get("fundamental_analysis") or {}
    market_sentiment = state.get("market_sentiment") or {}
    scenarios: list[Scenario] = state.get("scenarios") or []
    statuses = list(state.get("agent_statuses") or [])

    evidence_dump = [item.model_dump() for item in evidence]
    available_evidence_ids = {item["id"] for item in evidence_dump}

    # ── validation (pure Python, always runs) ─────────────────────────────
    errors: list[str] = []
    errors.extend(validate_scenario_scores(scenarios))
    errors.extend(validate_evidence_completeness(evidence_dump))
    errors.extend(validate_claim_coverage(fundamental_analysis, available_evidence_ids))
    errors.extend(validate_claim_coverage(market_sentiment, available_evidence_ids))

    warnings: list[str] = []
    if fundamental_analysis.get("missing_fields"):
        warnings.append(f"Missing fields: {', '.join(fundamental_analysis['missing_fields'])}")
    if market_sentiment.get("missing_fields"):
        warnings.append(f"Missing sentiment fields: {', '.join(market_sentiment['missing_fields'])}")

    # ── LLM report generation ─────────────────────────────────────────────
    report_markdown: str | None = None

    if evidence:
        prompt = _build_prompt(intent, evidence_dump, fundamental_analysis, market_sentiment, scenarios)
        try:
            raw = _llm.complete_text(prompt, system=_SYSTEM)
            if raw and len(raw.strip()) > 100:
                report_markdown = raw.strip()
                if errors:
                    report_markdown += "\n\n## Validation Warnings\n" + "\n".join(
                        f"- {e}" for e in errors
                    )
        except Exception as exc:
            logger.warning("report_verification LLM failed: %s", exc)

    if report_markdown is None:
        logger.warning("report_verification falling back to template report")
        report_markdown = _fallback_report(intent, evidence_dump, fundamental_analysis, market_sentiment, scenarios)
        if errors:
            report_markdown += "\n\n## Validation Warnings\n" + "\n".join(f"- {e}" for e in errors)

    report_json = {
        "intent": intent.model_dump() if intent else {},
        "evidence": evidence_dump,
        "fundamental_analysis": fundamental_analysis,
        "market_sentiment": market_sentiment,
        "scenarios": [s.model_dump() for s in scenarios],
        "validation": {"errors": errors, "warnings": warnings},
    }

    # Surface unsupported claims as open questions so the graph can request a
    # supplementary research pass if the retry budget allows.
    citation_errors = [e for e in errors if "unknown evidence" in e or "missing evidence" in e]
    open_questions: list[str] = [
        f"report_verification: {e}" for e in citation_errors
    ]

    if statuses:
        statuses = update_status(
            statuses, "report_verification",
            status="completed", action="report published",
            details=[
                f"is_valid={not errors}",
                f"errors={len(errors)}",
                f"retry_questions={len(open_questions)}",
            ],
        )

    return {
        "report_markdown": report_markdown,
        "report_json": report_json,
        "validation_result": ValidationResult(
            is_valid=not errors, errors=errors, warnings=warnings
        ),
        "open_questions": open_questions,
        "agent_statuses": statuses,
    }


