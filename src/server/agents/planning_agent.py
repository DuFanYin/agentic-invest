"""Planning agent — understands the research question and produces an actionable research plan."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from src.server.models.analysis import CustomSection, PlanContext, ReportPlan, ReportSection
from src.server.models.intent import ResearchIntent
from src.server.models.state import ResearchState
from src.server.prompts import build_prompt
from src.server.services.llm_provider import LLMClient
from src.server.utils.contract import NODE_CONTRACTS, assert_reads, assert_writes
from src.server.utils.status import initial_agent_statuses, update_status

_READS = NODE_CONTRACTS["planner"].reads
_WRITES = NODE_CONTRACTS["planner"].writes

logger = logging.getLogger(__name__)

_NODE = "planner"


@dataclass
class PlanningResult:
    intent: ResearchIntent
    research_focus: list[str]
    must_have_metrics: list[str]
    plan_notes: list[str]
    report_plan: ReportPlan
    custom_sections: list[CustomSection]


# ── Default report plans per query type ────────────────────────────────────


def _default_report_plan(report_type: str = "general") -> ReportPlan:
    base_sections = [
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
    return ReportPlan(report_type=report_type, sections=base_sections)


def _parse_custom_sections(raw: dict) -> list[CustomSection]:
    items = raw.get("custom_sections") or []
    seen_ids: set[str] = set()
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or item.get("section_id", "")).strip().replace(" ", "_")
        title = str(item.get("title", "")).strip()
        focus = str(item.get("focus", "")).strip()
        if not sid or not title or not focus or sid in seen_ids:
            continue
        seen_ids.add(sid)
        result.append(CustomSection(id=sid, title=title, focus=focus))
    return result[:3]


def _fallback_custom_section(query: str) -> CustomSection:
    """Ensures at least one custom section when the model omits or invalidates them."""
    q = (query or "").strip() or "the user's research question"
    return CustomSection(
        id="query_specific_deep_dive",
        title="Query-specific analysis",
        focus=f"Answer the user's core question directly with evidence-backed reasoning: {q}",
    )


# ── Core plan() function ────────────────────────────────────────────────────


async def plan(query: str, llm_client: LLMClient) -> PlanningResult:
    system, prompt = build_prompt("planner", "main", query=query)
    try:
        raw = await llm_client.complete(prompt, system=system, node=_NODE)
        parsed = json.loads(raw)

        intent = ResearchIntent(
            intent=parsed.get("intent", "investment_research"),
            subjects=parsed.get("subjects") or [query],
            scope=parsed.get("scope", "theme"),
            ticker=parsed.get("ticker"),
            time_horizon=parsed.get("time_horizon"),
        )

        research_focus = [s for s in (parsed.get("research_focus") or []) if isinstance(s, str)]
        must_have_metrics = [s for s in (parsed.get("must_have_metrics") or []) if isinstance(s, str)]
        plan_notes = [s for s in (parsed.get("plan_notes") or []) if isinstance(s, str)]

        if not research_focus:
            research_focus = [f"Analyse {', '.join(intent.subjects)} for {intent.intent.replace('_', ' ')}"]
        if not must_have_metrics:
            must_have_metrics = ["revenue", "gross_margin_pct", "pe_ratio", "free_cash_flow"]
        if not plan_notes:
            plan_notes = [f"Horizon: {intent.time_horizon or 'unspecified'}"]

        report_plan = _default_report_plan()
        custom_sections = _parse_custom_sections(parsed)
        if not custom_sections:
            logger.warning("%s: no valid custom_sections (expected 1-3); using fallback", _NODE)
            custom_sections = [_fallback_custom_section(query)]

        return PlanningResult(
            intent=intent,
            research_focus=research_focus,
            must_have_metrics=must_have_metrics,
            plan_notes=plan_notes,
            report_plan=report_plan,
            custom_sections=custom_sections,
        )

    except Exception:
        logger.warning("%s: planning failed, using fallback", _NODE, exc_info=True)
        intent = ResearchIntent(
            intent="investment_research", subjects=[query], scope="theme", ticker=None, time_horizon=None
        )
        return PlanningResult(
            intent=intent,
            research_focus=[f"General investment analysis: {query}"],
            must_have_metrics=["revenue", "gross_margin_pct", "pe_ratio", "free_cash_flow"],
            plan_notes=["No specific plan generated — LLM parse failed"],
            report_plan=_default_report_plan("general"),
            custom_sections=[_fallback_custom_section(query)],
        )


def make_planning_node(llm_client: LLMClient):
    async def planning_agent_node(state: ResearchState) -> ResearchState:
        assert_reads(state, _READS, _NODE)
        statuses = initial_agent_statuses(running=_NODE)
        result = await plan(state["query"], llm_client)

        statuses = update_status(
            statuses,
            _NODE,
            lifecycle="standby",
            phase="idle",
            action="plan ready",
            details=[
                f"intent={result.intent.intent}",
                f"scope={result.intent.scope}",
                f"report_type={result.report_plan.report_type}",
                f"custom={len(result.custom_sections)}",
            ],
        )
        statuses = update_status(
            statuses, "research", lifecycle="active", phase="collecting_evidence", action="collecting evidence"
        )
        delta = {
            "intent": result.intent,
            "plan_context": PlanContext(
                research_focus=result.research_focus,
                must_have_metrics=result.must_have_metrics,
                plan_notes=result.plan_notes,
                report_plan=result.report_plan,
                custom_sections=result.custom_sections,
            ),
            "research_iteration": 0,
            "retry_questions": [],
            "agent_statuses": statuses,
        }
        assert_writes(delta, _WRITES, _NODE)
        return delta

    return planning_agent_node
