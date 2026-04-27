"""Report generation and verification node — LLM writes the Markdown report."""

from __future__ import annotations

import json
import logging

from src.server.models.analysis import FundamentalAnalysis, MarketSentiment
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
_NODE = "report_verification"

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
    "## Scenario Implications",
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

    sc_lines = "\n".join(
        f"- {s.name} ({s.probability:.0%}) [{', '.join(s.tags)}]: {s.description}"
        for s in scenarios
    )

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


def report_verification_node(state: ResearchState, *, llm: OpenRouterClient = _default_llm) -> ResearchState:
    intent = state.get("intent")
    evidence = state.get("evidence") or []
    fundamental_analysis = state.get("fundamental_analysis") or {}
    market_sentiment = state.get("market_sentiment") or {}
    scenarios: list[Scenario] = state.get("scenarios") or []
    statuses = list(state.get("agent_statuses") or [])
    if statuses:
        statuses = update_status(
            statuses, "report_verification",
            lifecycle="active", phase="generating_report", action="running validation and report generation",
        )

    evidence_dump = [item.model_dump() for item in evidence]
    available_evidence_ids = {item["id"] for item in evidence_dump}

    # ── validation (pure Python, always runs) ─────────────────────────────
    errors: list[str] = []
    errors.extend(validate_scenario_scores(scenarios))
    errors.extend(validate_evidence_completeness(evidence_dump))
    errors.extend(validate_claim_coverage(fundamental_analysis, available_evidence_ids))
    errors.extend(validate_claim_coverage(market_sentiment, available_evidence_ids))

    warnings: list[str] = []
    fa_missing = fundamental_analysis.missing_fields if isinstance(fundamental_analysis, FundamentalAnalysis) else []
    ms_missing = market_sentiment.missing_fields if isinstance(market_sentiment, MarketSentiment) else []
    if fa_missing:
        warnings.append(f"Missing fields: {', '.join(fa_missing)}")
    if ms_missing:
        warnings.append(f"Missing sentiment fields: {', '.join(ms_missing)}")

    # ── LLM report generation ─────────────────────────────────────────────
    report_markdown: str | None = None

    if evidence:
        prompt = _build_prompt(intent, evidence_dump, fundamental_analysis, market_sentiment, scenarios)
        try:
            raw = llm.complete_text(prompt, system=_SYSTEM)
            if raw and len(raw.strip()) > 100:
                report_markdown = raw.strip()
                if errors:
                    report_markdown += "\n\n## Validation Warnings\n" + "\n".join(
                        f"- {e}" for e in errors
                    )
        except Exception as exc:
            logger.warning("report_verification LLM failed: %s", exc)

    if report_markdown is None:
        msg = f"[{_NODE}] unable to generate report markdown from LLM"
        logger.error(msg)
        if statuses:
            statuses = update_status(
                statuses,
                "report_verification",
                lifecycle="failed",
                phase="generating_report",
                action="report generation failed",
                last_error=msg,
            )
        raise RuntimeError(msg)

    report_json = {
        "intent": intent.model_dump() if intent else {},
        "evidence": evidence_dump,
        "fundamental_analysis": fundamental_analysis.model_dump() if isinstance(fundamental_analysis, FundamentalAnalysis) else {},
        "market_sentiment": market_sentiment.model_dump() if isinstance(market_sentiment, MarketSentiment) else {},
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
            lifecycle="standby", phase="generating_report", action="report published",
            details=[
                f"is_valid={not errors}",
                f"errors={len(errors)}",
                f"retry_questions={len(open_questions)}",
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
        "open_questions": open_questions,
        "agent_statuses": statuses,
    }


