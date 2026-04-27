"""Planning agent — understands the research question and produces an actionable research plan."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

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
  "required_outputs": ["valuation","risks","scenarios"],

  "research_focus": [
    "What specific angle or question should analysts focus on?",
    "e.g. 'Is NVDA valuation justified given AI capex cycle?'",
    "e.g. 'Assess margin sustainability under rising competition'"
  ],
  "must_have_metrics": [
    "List metrics that are essential to answer this question",
    "e.g. 'revenue_growth_yoy', 'gross_margin_pct', 'free_cash_flow', 'debt_to_equity'"
  ],
  "plan_notes": [
    "Any specific concerns, risks, or angles the research must address",
    "e.g. 'Check whether guidance was revised at last earnings'",
    "e.g. 'Macro sensitivity: how does this thesis hold if rates stay elevated?'",
    "e.g. 'Compare vs sector peers on valuation multiples'"
  ]
}

Rules:
- research_focus: 2-4 specific, actionable focus areas derived from the user's question.
  Do NOT write generic statements like "analyse the company fundamentals".
  Instead write what the specific angle IS for this particular query.
- must_have_metrics: 3-6 metric names that are genuinely critical for this analysis.
  Use snake_case names matching financial data fields (e.g. pe_ratio, revenue, ebitda_margin).
- plan_notes: 2-4 specific analytical questions or risk flags the team must address.
  These should reflect the nuance in the user's query, not generic investment platitudes.
- If the query is macro/thematic (no ticker), still produce a meaningful plan.
"""


@dataclass
class PlanningResult:
    intent: ResearchIntent
    research_focus: list[str]
    must_have_metrics: list[str]
    plan_notes: list[str]


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

        # Fallback: if LLM gave empty planning fields, derive minimal ones from intent
        if not research_focus:
            research_focus = [f"Analyse {', '.join(intent.subjects)} for {intent.intent.replace('_', ' ')}"]
        if not must_have_metrics:
            must_have_metrics = ["revenue", "gross_margin_pct", "pe_ratio", "free_cash_flow"]
        if not plan_notes:
            plan_notes = [f"Horizon: {intent.time_horizon or 'unspecified'}"]

        return PlanningResult(
            intent=intent,
            research_focus=research_focus,
            must_have_metrics=must_have_metrics,
            plan_notes=plan_notes,
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
        )


# Keep this as a convenience shim so test_intent.py import still works
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
                f"focus={len(result.research_focus)}",
                f"metrics={len(result.must_have_metrics)}",
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
            "research_iteration": 0,
            "retry_questions": [],
            "agent_statuses": statuses,
        }
    return planning_agent_node
