"""Fundamental analysis node — LLM-grounded claims over real evidence and metrics."""

from __future__ import annotations

import json
import logging

from src.server.models.analysis import BusinessQuality, FundamentalAnalysis, Valuation
from src.server.models.state import ResearchState
from src.server.prompts import build_prompt
from src.server.services.llm_provider import LLMClient
from src.server.utils.contract import NODE_CONTRACTS, assert_reads, assert_writes
from src.server.utils.status import update_status

_READS = NODE_CONTRACTS["fundamental_analysis"].reads
_WRITES = NODE_CONTRACTS["fundamental_analysis"].writes

logger = logging.getLogger(__name__)

_default_llm = LLMClient()
_NODE = "fundamental_analysis"


def _build_prompt(
    evidence,
    metrics,
    intent,
    research_focus,
    must_have_metrics,
    plan_notes,
) -> tuple[str, str]:
    financial_evidence = [ev for ev in evidence if ev.source_type == "financial_api"]
    supplemental = [ev for ev in evidence if ev.source_type != "financial_api"]

    ev_lines = (
        "\n".join(
            f"[{ev.id}] (reliability={ev.reliability}) {ev.summary}"
            for ev in financial_evidence
        )
        or "No financial API evidence available."
    )

    supplemental_lines = "\n".join(
        f"[{ev.id}] ({ev.source_type}) {ev.summary}" for ev in supplemental
    )[:2000]  # cap to avoid token bloat

    metrics_json = json.dumps(metrics, indent=2) if metrics else "{}"

    intent_str = ""
    if intent:
        intent_str = (
            f"Ticker: {intent.ticker or 'unknown'} | "
            f"Scope: {intent.scope} | "
            f"Horizon: {intent.time_horizon or 'unspecified'}"
        )

    focus_str = (
        "\n".join(f"- {f}" for f in research_focus)
        if research_focus
        else "General fundamental analysis"
    )
    metrics_str = (
        ", ".join(must_have_metrics) if must_have_metrics else "standard financials"
    )
    notes_str = "\n".join(f"- {n}" for n in plan_notes) if plan_notes else "none"
    supp = supplemental_lines or "none"

    return build_prompt(
        "fundamental_analysis",
        "main",
        intent_str=intent_str,
        focus_str=focus_str,
        metrics_str=metrics_str,
        notes_str=notes_str,
        ev_lines=ev_lines,
        metrics_json=metrics_json,
        supplemental_lines=supp,
    )


async def fundamental_analysis_node(
    state: ResearchState, *, llm: LLMClient = _default_llm
) -> ResearchState:
    assert_reads(state, _READS, _NODE)

    evidence = state.get("evidence") or []
    normalized_data = state.get("normalized_data")
    intent = state.get("intent")
    plan_ctx = state.get("plan_context")
    research_focus: list[str] = plan_ctx.research_focus if plan_ctx else []
    must_have_metrics: list[str] = plan_ctx.must_have_metrics if plan_ctx else []
    plan_notes: list[str] = plan_ctx.plan_notes if plan_ctx else []
    statuses = list(state.get("agent_statuses") or [])
    if statuses:
        statuses = update_status(
            statuses,
            "fundamental_analysis",
            lifecycle="active",
            phase="analyzing_fundamentals",
            action="building analysis",
        )

    metrics = normalized_data.metrics.model_dump() if normalized_data else {}
    result: FundamentalAnalysis | None = None

    if evidence:
        system, prompt = _build_prompt(
            evidence,
            metrics,
            intent,
            research_focus,
            must_have_metrics,
            plan_notes,
        )
        try:
            raw = await llm.call_with_retry(prompt, system=system, node=_NODE)
            parsed = json.loads(raw)
            parsed["metrics"] = metrics
            result = FundamentalAnalysis.model_validate(parsed)
        except Exception as exc:
            logger.warning("%s: LLM step failed — %s", _NODE, exc)

    if result is None:
        logger.warning("%s: LLM exhausted — returning degraded result", _NODE)
        result = FundamentalAnalysis(
            claims=[],
            business_quality=BusinessQuality(view="stable"),
            valuation=Valuation(relative_multiple_view="unavailable"),
            degraded=True,
        )
        if statuses:
            statuses = update_status(
                statuses,
                "fundamental_analysis",
                lifecycle="degraded",
                phase="analyzing_fundamentals",
                action="fundamental analysis degraded",
            )

    if statuses:
        statuses = update_status(
            statuses,
            "fundamental_analysis",
            lifecycle="standby",
            phase="analyzing_fundamentals",
            action="fundamentals ready",
            details=[f"claims={len(result.claims)}"],
        )
        statuses = update_status(
            statuses,
            "llm_judge",
            lifecycle="active",
            phase="evaluating_readiness",
            action="reviewing analysis readiness",
        )

    delta = {
        "fundamental_analysis": result,
        "agent_statuses": statuses,
    }
    assert_writes(delta, _WRITES, "fundamental_analysis")
    return delta
