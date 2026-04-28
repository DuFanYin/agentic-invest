"""
LangGraph-based orchestrator.

Graph topology
──────────────
                         ┌──────────────────────────────────────────────────────┐
                         │  (retry: retry_questions detected, iteration < 2)    │
                         ▼                                                      │
START → parse_intent → research → [parallel] ───────────────────────────────── ┤
                         ▲         fundamental_analysis                         │
                         │         macro_analysis                              │
                         │         market_sentiment                            │
                         │       → retry_gate ── (structural|conflict gap?) ──┘
                         │                   └── (no gaps) → scenario_scoring
                         │                                    → scenario_debate
                         │                                    → report_finalize
                         │                                        │ (unsupported claims
                         └────────────────────────────────────────┘  + pass < 2)
                                                                    └── END
"""

import asyncio
from collections.abc import AsyncGenerator

from langgraph.graph import END, START, StateGraph

from src.server.agents.fundamental_analysis import fundamental_analysis_node
from src.server.agents.macro_analysis import macro_analysis_node
from src.server.agents.planning_agent import make_planning_node
from src.server.agents.market_sentiment import market_sentiment_node
from src.server.agents.report_finalize import report_finalize_node
from src.server.agents.research import research_node
from src.server.agents.retry_gate import (
    MAX_RESEARCH_ITERATIONS,
    retry_gate_node,
    retry_router,
)
from src.server.agents.scenario_debate import scenario_debate_node
from src.server.agents.scenario_scoring import scenario_scoring_node
from src.server.models.request import ResearchRequest
from src.server.models.response import LLMCall, ResearchResponse, ValidationResult
from src.server.models.state import ResearchState
from src.server.services.collector import LLMCallCollector
from src.server.services.openrouter import OpenRouterClient
from src.server.services.section_queue import SectionQueue


def _report_router(state: ResearchState) -> str:
    """Re-route to research if report_finalize found unsupported claims and
    we still have retry budget; otherwise terminate."""
    if state.get("retry_questions") and state.get("research_iteration", 0) < MAX_RESEARCH_ITERATIONS:
        return "research"
    return END


# ── graph builder ──────────────────────────────────────────────────────────

def build_graph(llm_client: OpenRouterClient | None = None, sq: SectionQueue | None = None) -> StateGraph:
    llm_client = llm_client or OpenRouterClient()
    sq = sq  # captured by _report_node closure

    builder = StateGraph(ResearchState)

    async def _fundamental_node(state: ResearchState) -> ResearchState:
        return await fundamental_analysis_node(state, llm=llm_client)

    async def _macro_node(state: ResearchState) -> ResearchState:
        return await macro_analysis_node(state, llm=llm_client)

    async def _sentiment_node(state: ResearchState) -> ResearchState:
        return await market_sentiment_node(state, llm=llm_client)

    async def _scenario_node(state: ResearchState) -> ResearchState:
        return await scenario_scoring_node(state, llm=llm_client)

    async def _debate_node(state: ResearchState) -> ResearchState:
        return await scenario_debate_node(state, llm=llm_client)

    async def _report_node(state: ResearchState) -> ResearchState:
        return await report_finalize_node(state, llm=llm_client, section_queue=sq)

    builder.add_node("parse_intent", make_planning_node(llm_client))
    builder.add_node("research", research_node)
    builder.add_node("fundamental_analysis", _fundamental_node)
    builder.add_node("macro_analysis", _macro_node)
    builder.add_node("market_sentiment", _sentiment_node)
    builder.add_node("retry_gate", retry_gate_node)
    builder.add_node("scenario_scoring", _scenario_node)
    builder.add_node("scenario_debate", _debate_node)
    builder.add_node("report_finalize", _report_node)

    builder.add_edge(START, "parse_intent")
    builder.add_edge("parse_intent", "research")

    # Fan-out: research → three analysis nodes in parallel
    builder.add_edge("research", "fundamental_analysis")
    builder.add_edge("research", "macro_analysis")
    builder.add_edge("research", "market_sentiment")

    # Fan-in: all three analysis nodes → retry_gate
    builder.add_edge("fundamental_analysis", "retry_gate")
    builder.add_edge("macro_analysis", "retry_gate")
    builder.add_edge("market_sentiment", "retry_gate")

    builder.add_conditional_edges(
        "retry_gate",
        retry_router,
        {"research": "research", "scenario_scoring": "scenario_scoring"},
    )

    builder.add_edge("scenario_scoring", "scenario_debate")
    builder.add_edge("scenario_debate", "report_finalize")
    builder.add_conditional_edges(
        "report_finalize",
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

    async def run(self, request: ResearchRequest) -> ResearchResponse:
        collector = LLMCallCollector()
        sq = SectionQueue()
        client = self._client_for_request(collector)
        graph = build_graph(client, sq=sq)
        final_state = await graph.ainvoke({"query": request.query})
        cost, prompt_tok, completion_tok = collector.totals()
        return _state_to_response(final_state, llm_calls=collector.all(), total_cost_usd=cost, total_prompt_tokens=prompt_tok, total_completion_tokens=completion_tok)

    async def run_stream(self, request: ResearchRequest) -> AsyncGenerator[dict, None]:
        collector = LLMCallCollector()
        sq = SectionQueue()
        client = self._client_for_request(collector)
        graph = build_graph(client, sq=sq)
        final_state: ResearchState = {}
        stream_iter = graph.astream(
            {"query": request.query},
            stream_mode=["updates", "values"],
        ).__aiter__()
        step_task: asyncio.Task | None = asyncio.create_task(anext(stream_iter))
        call_task: asyncio.Task | None = asyncio.create_task(collector.wait_next())
        section_task: asyncio.Task | None = asyncio.create_task(sq._q.get())
        graph_done = False

        try:
            while True:
                wait_tasks = [t for t in (step_task, call_task, section_task) if t is not None]
                if not wait_tasks:
                    break

                done, _ = await asyncio.wait(wait_tasks, return_when=asyncio.FIRST_COMPLETED)

                if call_task is not None and call_task in done:
                    item = call_task.result()
                    yield {"type": "llm_call", "payload": item.model_dump()}
                    call_task = asyncio.create_task(collector.wait_next())

                if section_task is not None and section_task in done:
                    item = section_task.result()
                    if item is not sq._SENTINEL:
                        yield {"type": "section_ready", "payload": item}
                        section_task = asyncio.create_task(sq._q.get())
                    else:
                        section_task = None  # queue exhausted

                if step_task is not None and step_task in done:
                    try:
                        mode, payload = step_task.result()
                    except StopAsyncIteration:
                        graph_done = True
                        step_task = None
                    else:
                        if mode == "updates":
                            for _, delta in payload.items():
                                agent_statuses = delta.get("agent_statuses")
                                if agent_statuses:
                                    yield {
                                        "type": "agent_status",
                                        "payload": [s.model_dump() for s in agent_statuses],
                                    }
                        elif mode == "values":
                            final_state = payload
                        step_task = asyncio.create_task(anext(stream_iter))

                if graph_done and collector.pending_count() == 0 and section_task is None:
                    if call_task is not None:
                        call_task.cancel()
                        try:
                            await call_task
                        except asyncio.CancelledError:
                            pass
                        call_task = None
                    break
        finally:
            for task in (step_task, call_task, section_task):
                if task is not None and not task.done():
                    task.cancel()
            for task in (step_task, call_task, section_task):
                if task is not None:
                    try:
                        await task
                    except (asyncio.CancelledError, StopAsyncIteration, Exception):
                        pass

        all_llm_calls = collector.all()
        cost, prompt_tok, completion_tok = collector.totals()
        response = _state_to_response(final_state, llm_calls=all_llm_calls, total_cost_usd=cost, total_prompt_tokens=prompt_tok, total_completion_tokens=completion_tok)
        yield {"type": "final", "payload": response}


# ── helpers ────────────────────────────────────────────────────────────────

def _state_to_response(state: ResearchState, *, llm_calls: list[LLMCall] | None = None, total_cost_usd: float = 0.0, total_prompt_tokens: int = 0, total_completion_tokens: int = 0) -> ResearchResponse:
    from src.server.models.analysis import (
        FundamentalAnalysis,
        MacroAnalysis,
        MarketSentiment,
        ScenarioDebate,
    )
    fa = state.get("fundamental_analysis")
    macro = state.get("macro_analysis")
    ms = state.get("market_sentiment")
    debate = state.get("scenario_debate")
    return ResearchResponse(
        report_markdown=state.get("report_markdown", ""),
        report_json=state.get("report_json", {}),
        intent=state.get("intent"),
        evidence=state.get("evidence") or [],
        fundamental_analysis=fa if isinstance(fa, FundamentalAnalysis) else None,
        macro_analysis=macro if isinstance(macro, MacroAnalysis) else None,
        market_sentiment=ms if isinstance(ms, MarketSentiment) else None,
        scenarios=state.get("scenarios") or [],
        scenario_debate=debate if isinstance(debate, ScenarioDebate) else None,
        agent_statuses=state.get("agent_statuses") or [],
        validation_result=state.get("validation_result") or ValidationResult(),
        llm_calls=llm_calls or [],
        total_cost_usd=total_cost_usd,
        total_prompt_tokens=total_prompt_tokens,
        total_completion_tokens=total_completion_tokens,
        narrative_sections=state.get("narrative_sections") or {},
    )
