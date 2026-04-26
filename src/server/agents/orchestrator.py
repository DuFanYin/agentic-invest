"""
LangGraph-based orchestrator.

Graph topology
──────────────
                         ┌──────────────────────────────────────────────┐
                         │  (retry: open_questions detected, pass < 2)  │
                         ▼                                              │
START → parse_intent → research → [parallel] ─────────────────────────┤
                                   fundamental_analysis                 │
                                   market_sentiment                     │
                                 → gap_check ─── (gaps?) ──────────────┘
                                             └── (no gaps) → scenario_scoring
                                                              → report_verification → END
"""

import json
from collections.abc import Generator

from langgraph.graph import END, START, StateGraph

from src.server.agents.fundamental_analysis import fundamental_analysis_node
from src.server.agents.market_sentiment import market_sentiment_node
from src.server.agents.report_verification import report_verification_node
from src.server.agents.research import research_node
from src.server.agents.scenario_scoring import scenario_scoring_node
from src.server.models.intent import ResearchIntent
from src.server.models.request import ResearchRequest
from src.server.models.response import AgentStatus, ResearchResponse, ValidationResult
from src.server.models.state import ResearchState
from src.server.services.openrouter import OpenRouterClient
from src.server.utils.status import initial_agent_statuses, update_status

_MAX_RESEARCH_PASSES = 2


# ── intent parsing node ────────────────────────────────────────────────────

def _make_parse_intent_node(llm_client: OpenRouterClient):
    def parse_intent_node(state: ResearchState) -> ResearchState:
        statuses = initial_agent_statuses(running="parse_intent")
        intent = _parse_intent(state["query"], llm_client)
        statuses = update_status(
            statuses, "parse_intent",
            status="completed", action="intent parsed",
            details=[f"intent={intent.intent}", f"scope={intent.scope}"],
        )
        statuses = update_status(
            statuses, "research",
            status="running", action="collecting evidence",
        )
        return {
            "intent": intent,
            "research_pass": 0,
            "open_questions": [],
            "agent_statuses": statuses,
        }
    return parse_intent_node


# ── gap detection node + router ────────────────────────────────────────────

def _gap_check_node(state: ResearchState) -> ResearchState:
    intent = state.get("intent")
    fundamental_analysis = state.get("fundamental_analysis") or {}
    market_sentiment = state.get("market_sentiment") or {}
    current_pass = state.get("research_pass", 1)
    statuses = list(state.get("agent_statuses") or [])

    new_questions: list[str] = []
    if intent and not intent.ticker:
        new_questions.append("Need clearer company/ticker mapping from query context")
    if intent and not intent.time_horizon:
        new_questions.append("Need explicit investment horizon to refine scenario assumptions")
    if fundamental_analysis.get("missing_fields"):
        new_questions.append(
            "Need additional data for missing fundamental fields: "
            + ", ".join(fundamental_analysis["missing_fields"])
        )
    if market_sentiment.get("missing_fields"):
        new_questions.append(
            "Need additional sentiment evidence for: "
            + ", ".join(market_sentiment["missing_fields"])
        )

    # Cap retries — after max passes, clear questions so the router proceeds.
    if current_pass >= _MAX_RESEARCH_PASSES:
        new_questions = []

    will_retry = bool(new_questions)

    if statuses:
        statuses = update_status(
            statuses, "gap_check",
            status="completed",
            action="retrying research" if will_retry else "gaps resolved",
            details=[f"open_questions={len(new_questions)}"],
        )
        if will_retry:
            statuses = update_status(
                statuses, "research",
                status="running", action="supplementary evidence collection",
            )
        else:
            statuses = update_status(
                statuses, "scenario_scoring",
                status="running", action="scoring scenarios",
            )

    return {"open_questions": new_questions, "agent_statuses": statuses}


def _gap_router(state: ResearchState) -> str:
    if state.get("open_questions"):
        return "research"
    return "scenario_scoring"


# ── graph builder ──────────────────────────────────────────────────────────

def build_graph(llm_client: OpenRouterClient | None = None) -> StateGraph:
    llm_client = llm_client or OpenRouterClient()

    builder = StateGraph(ResearchState)

    builder.add_node("parse_intent", _make_parse_intent_node(llm_client))
    builder.add_node("research", research_node)
    builder.add_node("fundamental_analysis", fundamental_analysis_node)
    builder.add_node("market_sentiment", market_sentiment_node)
    builder.add_node("gap_check", _gap_check_node)
    builder.add_node("scenario_scoring", scenario_scoring_node)
    builder.add_node("report_verification", report_verification_node)

    builder.add_edge(START, "parse_intent")
    builder.add_edge("parse_intent", "research")

    # Fan-out: research → both analysis nodes in parallel
    builder.add_edge("research", "fundamental_analysis")
    builder.add_edge("research", "market_sentiment")

    # Fan-in: both analysis nodes → gap check
    builder.add_edge("fundamental_analysis", "gap_check")
    builder.add_edge("market_sentiment", "gap_check")

    builder.add_conditional_edges(
        "gap_check",
        _gap_router,
        {"research": "research", "scenario_scoring": "scenario_scoring"},
    )

    builder.add_edge("scenario_scoring", "report_verification")
    builder.add_edge("report_verification", END)

    return builder.compile()


# ── public façade ──────────────────────────────────────────────────────────

class OrchestratorAgent:
    def __init__(self, llm_client: OpenRouterClient | None = None) -> None:
        self._llm_client = llm_client or OpenRouterClient()
        self.graph = build_graph(self._llm_client)

    def run(self, request: ResearchRequest) -> ResearchResponse:
        final_state = self.graph.invoke({"query": request.query})
        return _state_to_response(final_state)

    def run_stream(self, request: ResearchRequest) -> Generator[dict, None, None]:
        final_state: ResearchState = {}

        for step in self.graph.stream(
            {"query": request.query},
            stream_mode=["updates", "values"],
        ):
            mode, payload = step

            if mode == "updates":
                for node_name, delta in payload.items():
                    agent_statuses = delta.get("agent_statuses")
                    if agent_statuses:
                        yield {
                            "type": "agent_status",
                            "payload": [s.model_dump() for s in agent_statuses],
                        }
                    open_questions = delta.get("open_questions") or []
                    yield {
                        "type": "state_update",
                        "payload": {
                            "node": node_name,
                            "research_state": {"open_questions": open_questions},
                        },
                    }

            elif mode == "values":
                final_state = payload

        response = _state_to_response(final_state)
        yield {"type": "final", "payload": response}

    def _parse_intent(self, query: str) -> ResearchIntent:
        return _parse_intent(query, self._llm_client)


# ── helpers ────────────────────────────────────────────────────────────────

def _parse_intent(query: str, llm_client: OpenRouterClient) -> ResearchIntent:
    prompt = (
        "You are an investment research intent parser. "
        "Extract structured intent from the user query and return JSON only.\n"
        "Output schema:\n"
        "{\n"
        '  "intent": "investment_research|comparison|scenario_analysis|risk_review|valuation_check|market_event_analysis",\n'
        '  "subjects": ["..."],\n'
        '  "scope": "company|sector|theme|macro|event|mixed",\n'
        '  "ticker": "string|null",\n'
        '  "time_horizon": "string|null",\n'
        '  "risk_level": "low|medium|high|null",\n'
        '  "required_outputs": ["valuation","risks","scenarios"]\n'
        "}\n"
        f"Query: {query}"
    )
    try:
        raw = llm_client.complete(prompt)
        parsed = json.loads(raw)
        return ResearchIntent(
            intent=parsed.get("intent", "investment_research"),
            subjects=parsed.get("subjects") or [query],
            scope=parsed.get("scope", "theme"),
            ticker=parsed.get("ticker"),
            risk_level=parsed.get("risk_level"),
            time_horizon=parsed.get("time_horizon"),
            required_outputs=parsed.get("required_outputs") or ["valuation", "risks", "scenarios"],
        )
    except Exception:
        return ResearchIntent(
            intent="investment_research",
            subjects=[query],
            scope="theme",
            ticker=None,
            risk_level=None,
            time_horizon=None,
            required_outputs=["valuation", "risks", "scenarios"],
        )


def _state_to_response(state: ResearchState) -> ResearchResponse:
    return ResearchResponse(
        report_markdown=state.get("report_markdown", ""),
        report_json=state.get("report_json", {}),
        intent=state.get("intent"),
        evidence=state.get("evidence") or [],
        fundamental_analysis=state.get("fundamental_analysis") or {},
        market_sentiment=state.get("market_sentiment") or {},
        scenarios=state.get("scenarios") or [],
        agent_statuses=state.get("agent_statuses") or [],
        validation_result=state.get("validation_result") or ValidationResult(),
    )
