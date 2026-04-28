"""Planning agent — understands the research question and produces an actionable research plan."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from src.server.models.analysis import ReportPlan, ReportSection
from src.server.models.intent import ResearchIntent
from src.server.models.state import ResearchState
from src.server.services.openrouter import OpenRouterClient
from src.server.utils.status import initial_agent_statuses, update_status

logger = logging.getLogger(__name__)

_NODE = "parse_intent"

_SYSTEM = (
    "You are a senior investment research director. "
    "Your job is to read an investment question, understand what the user actually needs, "
    "and produce a structured research plan that guides a team of analysts. "
    "Return only valid JSON — no markdown, no prose outside the JSON."
)

_SCHEMA = """
Return exactly this JSON structure:
{
  "intent": "investment_research|comparison|scenario_analysis|risk_review|valuation_check|market_event_analysis",
  "subjects": ["..."],
  "scope": "company|sector|theme|macro|event|mixed",
  "ticker": "string|null",
  "time_horizon": "string|null",
  "risk_level": "low|medium|high|null",

  "research_focus": [
    "What specific angle or question should analysts focus on?",
    "e.g. 'Is NVDA valuation justified given AI capex cycle?'"
  ],
  "must_have_metrics": [
    "e.g. 'revenue_growth_yoy', 'gross_margin_pct', 'free_cash_flow'"
  ],
  "plan_notes": [
    "Any specific concerns, risks, or angles the research must address",
    "e.g. 'Macro sensitivity: how does this thesis hold if rates stay elevated?'"
  ],

  "report_plan": {
    "report_type": "valuation|comparison|risk_review|scenario|general",
    "sections": [
      {
        "id": "executive_summary",
        "title": "Executive Summary",
        "source": "all",
        "required": true
      },
      {
        "id": "fundamental_analysis",
        "title": "Fundamental Analysis",
        "source": "fundamental_analysis",
        "required": true
      },
      {
        "id": "macro_environment",
        "title": "Macro Environment",
        "source": "macro_analysis",
        "required": true
      },
      {
        "id": "market_sentiment",
        "title": "Market Sentiment",
        "source": "market_sentiment",
        "required": true
      },
      {
        "id": "scenarios",
        "title": "Future Scenarios",
        "source": "scenarios",
        "required": true
      },
      {
        "id": "scenario_debate",
        "title": "Scenario Calibration",
        "source": "scenario_debate",
        "required": true
      },
      {
        "id": "conclusion",
        "title": "Conclusion & What To Watch",
        "source": "all",
        "required": true
      }
    ]
  }
}

Rules:
- research_focus: 2-4 specific, actionable focus areas derived from the user's question.
  Do NOT write generic statements like "analyse the company fundamentals".
- must_have_metrics: 3-6 metric names critical for this analysis (snake_case).
- plan_notes: 2-4 specific questions or risk flags the team must address.
- report_plan.report_type: pick the type that best fits the query.
- report_plan.sections: customise for the query type. Rules:
    * Always include executive_summary and conclusion sections.
    * Always include scenarios and scenario_debate sections — these are mandatory.
    * source must be one of: fundamental_analysis, macro_analysis, market_sentiment,
      scenarios, scenario_debate, evidence, all.
    * For comparison queries: add a section with source "fundamental_analysis" titled "Peer Comparison".
    * For risk_review queries: add a section with source "fundamental_analysis" titled "Risk Deep-Dive".
    * For valuation queries: add a section with source "fundamental_analysis" titled "Valuation Analysis".
    * For macro/theme queries: weight macro_analysis sections more heavily.
    * 5-8 sections total. Do not add sections with no clear source.
"""


@dataclass
class PlanningResult:
    intent: ResearchIntent
    research_focus: list[str]
    must_have_metrics: list[str]
    plan_notes: list[str]
    report_plan: ReportPlan


# ── Default report plans per query type ────────────────────────────────────

def _default_report_plan(report_type: str = "general") -> ReportPlan:
    base_sections = [
        ReportSection(id="executive_summary",    title="Executive Summary",       source="all",                  required=True),
        ReportSection(id="fundamental_analysis", title="Fundamental Analysis",    source="fundamental_analysis", required=True),
        ReportSection(id="macro_environment",    title="Macro Environment",       source="macro_analysis",       required=True),
        ReportSection(id="market_sentiment",     title="Market Sentiment",        source="market_sentiment",     required=True),
        ReportSection(id="scenarios",            title="Future Scenarios",        source="scenarios",            required=True),
        ReportSection(id="scenario_debate",      title="Scenario Calibration",    source="scenario_debate",      required=True),
        ReportSection(id="conclusion",           title="Conclusion & What To Watch", source="all",              required=True),
    ]
    return ReportPlan(report_type=report_type, sections=base_sections)


def _parse_report_plan(raw: dict) -> ReportPlan:
    rp = raw.get("report_plan", {})
    report_type = rp.get("report_type", "general")
    raw_sections = rp.get("sections", [])
    if not raw_sections:
        return _default_report_plan(report_type)

    valid_sources = {
        "fundamental_analysis", "macro_analysis", "market_sentiment",
        "scenarios", "scenario_debate", "evidence", "all",
    }
    sections = []
    seen_ids = set()
    for s in raw_sections:
        sid = s.get("id", "")
        src = s.get("source", "all")
        if not sid or sid in seen_ids:
            continue
        if src not in valid_sources:
            src = "all"
        seen_ids.add(sid)
        sections.append(ReportSection(
            id=sid,
            title=s.get("title", sid.replace("_", " ").title()),
            source=src,
            required=bool(s.get("required", True)),
        ))

    # Enforce mandatory sections always present
    mandatory = {
        "executive_summary": ReportSection(id="executive_summary", title="Executive Summary", source="all", required=True),
        "scenarios":         ReportSection(id="scenarios",         title="Future Scenarios",  source="scenarios",      required=True),
        "scenario_debate":   ReportSection(id="scenario_debate",   title="Scenario Calibration", source="scenario_debate", required=True),
        "conclusion":        ReportSection(id="conclusion",        title="Conclusion & What To Watch", source="all", required=True),
    }
    section_ids = {s.id for s in sections}
    # Prepend missing executive_summary, append missing tail sections
    if "executive_summary" not in section_ids:
        sections.insert(0, mandatory["executive_summary"])
    for sid in ("scenarios", "scenario_debate", "conclusion"):
        if sid not in section_ids:
            sections.append(mandatory[sid])

    return ReportPlan(report_type=report_type, sections=sections)


# ── Core plan() function ────────────────────────────────────────────────────

async def plan(query: str, llm_client: OpenRouterClient) -> PlanningResult:
    prompt = f"{_SCHEMA}\n\nUser query: {query}"
    try:
        raw = await llm_client.complete(prompt, system=_SYSTEM, node=_NODE)
        parsed = json.loads(raw)

        intent = ResearchIntent(
            intent=parsed.get("intent", "investment_research"),
            subjects=parsed.get("subjects") or [query],
            scope=parsed.get("scope", "theme"),
            ticker=parsed.get("ticker"),
            risk_level=parsed.get("risk_level"),
            time_horizon=parsed.get("time_horizon"),
            required_outputs=parsed.get("required_outputs") or ["valuation", "risks", "scenarios"],
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

        report_plan = _parse_report_plan(parsed)

        return PlanningResult(
            intent=intent,
            research_focus=research_focus,
            must_have_metrics=must_have_metrics,
            plan_notes=plan_notes,
            report_plan=report_plan,
        )

    except Exception:
        logger.warning("%s: planning failed, using fallback", _NODE)
        intent = ResearchIntent(
            intent="investment_research",
            subjects=[query],
            scope="theme",
            ticker=None,
            risk_level=None,
            time_horizon=None,
            required_outputs=["valuation", "risks", "scenarios"],
        )
        return PlanningResult(
            intent=intent,
            research_focus=[f"General investment analysis: {query}"],
            must_have_metrics=["revenue", "gross_margin_pct", "pe_ratio", "free_cash_flow"],
            plan_notes=["No specific plan generated — LLM parse failed"],
            report_plan=_default_report_plan("general"),
        )


async def parse_intent(query: str, llm_client: OpenRouterClient) -> ResearchIntent:
    result = await plan(query, llm_client)
    return result.intent


def make_planning_node(llm_client: OpenRouterClient):
    async def planning_agent_node(state: ResearchState) -> ResearchState:
        statuses = initial_agent_statuses(running=_NODE)
        result = await plan(state["query"], llm_client)

        statuses = update_status(
            statuses, _NODE,
            lifecycle="active", phase="dispatching", action="plan ready",
            details=[
                f"intent={result.intent.intent}",
                f"scope={result.intent.scope}",
                f"report_type={result.report_plan.report_type}",
                f"sections={len(result.report_plan.sections)}",
            ],
        )
        statuses = update_status(
            statuses, "research",
            lifecycle="active", phase="collecting_evidence", action="collecting evidence",
        )
        return {
            "intent": result.intent,
            "research_focus": result.research_focus,
            "must_have_metrics": result.must_have_metrics,
            "plan_notes": result.plan_notes,
            "report_plan": result.report_plan,
            "research_iteration": 0,
            "retry_questions": [],
            "agent_statuses": statuses,
        }
    return planning_agent_node
