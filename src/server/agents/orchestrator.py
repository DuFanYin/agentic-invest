"""
LangGraph-based orchestrator.

Graph topology
──────────────
                         ┌──────────────────────────────────────────────────────┐
                         │  (retry: open_questions detected, pass < 2)          │
                         ▼                                                      │
START → parse_intent → research → [parallel] ───────────────────────────────── ┤
                         ▲         fundamental_analysis  (writes agent_questions)│
                         │         market_sentiment      (writes agent_questions)│
                         │       → gap_check ─── (gaps?) ──────────────────────┘
                         │                   └── (no gaps) → scenario_scoring
                         │                                    → report_verification
                         │                                        │ (unsupported claims
                         └────────────────────────────────────────┘  + pass < 2)
                                                                    └── END
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
from src.server.models.response import AgentStatus, LLMCall, ResearchResponse, ValidationResult
from src.server.models.state import ResearchState, _RESET
from src.server.services.collector import LLMCallCollector
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
            lifecycle="active", phase="dispatching", action="intent parsed",
            details=[f"intent={intent.intent}", f"scope={intent.scope}"],
        )
        statuses = update_status(
            statuses, "research",
            lifecycle="active", phase="collecting_evidence", action="collecting evidence",
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
    fundamental_analysis = state.get("fundamental_analysis")
    market_sentiment = state.get("market_sentiment")
    normalized_data = state.get("normalized_data")
    current_pass = state.get("research_pass", 1)
    statuses = list(state.get("agent_statuses") or [])

    # ── Structural checks (orchestrator-level) ─────────────────────────────
    new_questions: list[str] = []
    if intent and not intent.ticker:
        new_questions.append("Need clearer company/ticker mapping from query context")
    if intent and not intent.time_horizon:
        new_questions.append("Need explicit investment horizon to refine scenario assumptions")

    # ── Agent-sourced questions (surfaced by analysis nodes) ───────────────
    # _accumulate_or_reset appends normal writes; returning [_RESET] clears the
    # list. gap_check reads accumulated questions then resets for the next cycle.
    agent_questions: list[str] = list(state.get("agent_questions") or [])
    new_questions.extend(agent_questions)

    # ── Conflict signals from research ─────────────────────────────────────
    conflicts = normalized_data.conflicts if normalized_data else []
    if conflicts:
        new_questions.append(
            f"Conflicting evidence detected across {len(conflicts)} topic(s): "
            + ", ".join(c["topic"] for c in conflicts)
            + ". Supplementary research may resolve these."
        )

    # Cap retries — after max passes, clear questions so the router proceeds.
    if current_pass >= _MAX_RESEARCH_PASSES:
        new_questions = []

    will_retry = bool(new_questions)

    if statuses:
        statuses = update_status(
            statuses, "gap_check",
            lifecycle="waiting" if will_retry else "standby",
            phase="gap_retry_required" if will_retry else "gap_resolved",
            action="retrying research" if will_retry else "gaps resolved",
            details=[
                f"open_questions={len(new_questions)}",
                f"agent_sourced={len(agent_questions)}",
                f"conflicts={len(conflicts)}",
            ],
        )
        if will_retry:
            statuses = update_status(
                statuses, "research",
                lifecycle="active", phase="retrying_evidence", action="supplementary evidence collection",
            )
        else:
            statuses = update_status(
                statuses, "scenario_scoring",
                lifecycle="active", phase="scoring_scenarios", action="scoring scenarios",
            )

    return {
        "open_questions": new_questions,
        "agent_questions": [_RESET],  # sentinel: reducer clears the list for the next cycle
        "agent_statuses": statuses,
    }


def _gap_router(state: ResearchState) -> str:
    if state.get("open_questions"):
        return "research"
    return "scenario_scoring"


def _report_router(state: ResearchState) -> str:
    """Re-route to research if report_verification found unsupported claims and
    we still have retry budget; otherwise terminate."""
    if state.get("open_questions") and state.get("research_pass", 0) < _MAX_RESEARCH_PASSES:
        return "research"
    return END


# ── graph builder ──────────────────────────────────────────────────────────

def build_graph(llm_client: OpenRouterClient | None = None) -> StateGraph:
    llm_client = llm_client or OpenRouterClient()

    builder = StateGraph(ResearchState)

    builder.add_node("parse_intent", _make_parse_intent_node(llm_client))
    builder.add_node("research", research_node)
    builder.add_node("fundamental_analysis", lambda s: fundamental_analysis_node(s, llm=llm_client))
    builder.add_node("market_sentiment", lambda s: market_sentiment_node(s, llm=llm_client))
    builder.add_node("gap_check", _gap_check_node)
    builder.add_node("scenario_scoring", lambda s: scenario_scoring_node(s, llm=llm_client))
    builder.add_node("report_verification", lambda s: report_verification_node(s, llm=llm_client))

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
    builder.add_conditional_edges(
        "report_verification",
        _report_router,
        {"research": "research", END: END},
    )

    return builder.compile()


# ── public façade ──────────────────────────────────────────────────────────

class OrchestratorAgent:
    def __init__(self, llm_client: OpenRouterClient | None = None) -> None:
        # llm_client is only set in tests (a MagicMock stub).
        # Production always leaves this None and gets a fresh client per request.
        self._test_client = llm_client

    def _client_for_request(self, collector: LLMCallCollector) -> OpenRouterClient:
        if self._test_client is not None:
            return self._test_client  # test stub owns its own mock behaviour; collector unused
        return OpenRouterClient(collector=collector)

    def run(self, request: ResearchRequest) -> ResearchResponse:
        collector = LLMCallCollector()
        client = self._client_for_request(collector)
        graph = build_graph(client)
        final_state = graph.invoke({"query": request.query})
        return _state_to_response(final_state, llm_calls=collector.all())

    def run_stream(self, request: ResearchRequest) -> Generator[dict, None, None]:
        collector = LLMCallCollector()
        client = self._client_for_request(collector)
        graph = build_graph(client)
        final_state: ResearchState = {}

        for step in graph.stream(
            {"query": request.query},
            stream_mode=["updates", "values"],
        ):
            mode, payload = step

            if mode == "updates":
                for node_name, delta in payload.items():
                    for item in collector.drain():
                        yield {"type": "llm_call", "payload": item.model_dump()}
                    agent_statuses = delta.get("agent_statuses")
                    if agent_statuses:
                        yield {
                            "type": "agent_status",
                            "payload": [s.model_dump() for s in agent_statuses],
                        }

            elif mode == "values":
                final_state = payload

        all_llm_calls = collector.all()
        for item in collector.drain():
            yield {"type": "llm_call", "payload": item.model_dump()}
        response = _state_to_response(final_state, llm_calls=all_llm_calls)
        yield {"type": "final", "payload": response}


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
        raw = llm_client.complete(prompt, node="parse_intent")
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


def _state_to_response(state: ResearchState, *, llm_calls: list[LLMCall] | None = None) -> ResearchResponse:
    from src.server.models.analysis import FundamentalAnalysis, MarketSentiment
    fa = state.get("fundamental_analysis")
    ms = state.get("market_sentiment")
    return ResearchResponse(
        report_markdown=state.get("report_markdown", ""),
        report_json=state.get("report_json", {}),
        intent=state.get("intent"),
        evidence=state.get("evidence") or [],
        fundamental_analysis=fa.model_dump() if isinstance(fa, FundamentalAnalysis) else {},
        market_sentiment=ms.model_dump() if isinstance(ms, MarketSentiment) else {},
        scenarios=state.get("scenarios") or [],
        agent_statuses=state.get("agent_statuses") or [],
        validation_result=state.get("validation_result") or ValidationResult(),
        llm_calls=llm_calls or [],
    )
