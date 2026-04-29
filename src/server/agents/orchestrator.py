"""
LangGraph-based orchestrator.

Graph topology
──────────────
                         ┌─────────────────────────────────────────────────────────┐
                         │  (retry: retry_questions detected, iteration < max)      │
                         ▼                                                          │
START → planner → research → [parallel] ──────────────────────────────────── ┤
                         ▲         fundamental_analysis                             │
                         │         macro_analysis                                  │
                         │         market_sentiment                                │
                         │       → llm_judge ── (retry?) ──────────────────────────┘
                         │          │            └── (halt: all analyses degraded) → report_finalize
                         │          └── (continue) → scenario_scoring → scenario_debate → report_finalize
                         └───────────────────────────────────────────────┘
                                                                           └── END
"""

import asyncio
from collections.abc import AsyncGenerator

from langgraph.graph import END, START, StateGraph
from src.server.agents.fundamental_analysis import fundamental_analysis_node
from src.server.agents.llm_judge import llm_judge_node, llm_judge_router_fn
from src.server.agents.macro_analysis import macro_analysis_node
from src.server.agents.market_sentiment import market_sentiment_node
from src.server.agents.planning_agent import make_planning_node
from src.server.agents.report_finalize import report_finalize_node
from src.server.agents.research import research_node
from src.server.agents.scenario_debate import scenario_debate_node
from src.server.agents.scenario_scoring import scenario_scoring_node
from src.server.config import CACHE_DB_PATH, REQUEST_TIMEOUT_SECONDS
from src.server.models.analysis import FundamentalAnalysis, MacroAnalysis, MarketSentiment, ScenarioDebate
from src.server.models.request import ResearchRequest
from src.server.models.response import LLMCall, ResearchResponse, ValidationResult
from src.server.models.state import ResearchState
from src.server.services.cache import Cache
from src.server.services.collector import LLMCallCollector
from src.server.services.finance_data import FinanceDataClient
from src.server.services.llm_provider import LLMClient
from src.server.services.macro_data import MacroDataClient
from src.server.services.web_research import WebResearchClient

# ── graph builder ──────────────────────────────────────────────────────────


def build_graph(llm_client: LLMClient | None = None) -> StateGraph:
    llm_client = llm_client or LLMClient()
    cache = Cache(db_path=CACHE_DB_PATH)
    finance_client = FinanceDataClient()
    macro_client = MacroDataClient(cache=cache)
    web_client = WebResearchClient()

    builder = StateGraph(ResearchState)

    async def _fundamental_node(state: ResearchState) -> ResearchState:
        return await fundamental_analysis_node(state, llm=llm_client)

    async def _macro_node(state: ResearchState) -> ResearchState:
        return await macro_analysis_node(state, llm=llm_client)

    async def _sentiment_node(state: ResearchState) -> ResearchState:
        return await market_sentiment_node(state, llm=llm_client)

    async def _judge_node(state: ResearchState) -> ResearchState:
        return await llm_judge_node(state, llm=llm_client)

    async def _scenario_node(state: ResearchState) -> ResearchState:
        return await scenario_scoring_node(state, llm=llm_client)

    async def _debate_node(state: ResearchState) -> ResearchState:
        return await scenario_debate_node(state, llm=llm_client)

    async def _report_node(state: ResearchState) -> ResearchState:
        return await report_finalize_node(state, llm=llm_client)

    async def _research_node(state: ResearchState) -> ResearchState:
        return await research_node(
            state,
            llm=llm_client,
            cache=cache,
            finance_client=finance_client,
            macro_client=macro_client,
            web_client=web_client,
        )

    builder.add_node("planner", make_planning_node(llm_client))
    builder.add_node("research", _research_node)
    builder.add_node("fundamental_analysis", _fundamental_node)
    builder.add_node("macro_analysis", _macro_node)
    builder.add_node("market_sentiment", _sentiment_node)
    builder.add_node("llm_judge", _judge_node)
    builder.add_node("scenario_scoring", _scenario_node)
    builder.add_node("scenario_debate", _debate_node)
    builder.add_node("report_finalize", _report_node)

    builder.add_edge(START, "planner")
    builder.add_edge("planner", "research")

    # Fan-out: research → three analysis nodes in parallel
    builder.add_edge("research", "fundamental_analysis")
    builder.add_edge("research", "macro_analysis")
    builder.add_edge("research", "market_sentiment")

    # Fan-in: all three analysis nodes → llm_judge (assess+decide)
    builder.add_edge("fundamental_analysis", "llm_judge")
    builder.add_edge("macro_analysis", "llm_judge")
    builder.add_edge("market_sentiment", "llm_judge")

    builder.add_conditional_edges(
        "llm_judge",
        llm_judge_router_fn,
        {"research": "research", "scenario_scoring": "scenario_scoring", "report_finalize": "report_finalize"},
    )

    builder.add_edge("scenario_scoring", "scenario_debate")
    builder.add_edge("scenario_debate", "report_finalize")
    builder.add_edge("report_finalize", END)

    return builder.compile()


# ── public façade ──────────────────────────────────────────────────────────


class OrchestratorAgent:
    def __init__(self, llm_client: LLMClient | None = None) -> None:
        # llm_client is only set in tests (a MagicMock stub).
        # Production always leaves this None and gets a fresh client per request.
        self._test_client = llm_client

    def _client_for_request(self, collector: LLMCallCollector) -> LLMClient:
        if self._test_client is not None:
            return self._test_client  # test stub owns its own mock behaviour; collector unused
        return LLMClient(collector=collector)

    async def run(self, request: ResearchRequest) -> ResearchResponse:
        collector = LLMCallCollector()
        client = self._client_for_request(collector)
        graph = build_graph(client)
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                final_state = await graph.ainvoke({"query": request.query})
        except TimeoutError as exc:
            raise RuntimeError(f"[orchestrator] request timeout after {int(REQUEST_TIMEOUT_SECONDS)}s") from exc
        cost, prompt_tok, completion_tok = collector.totals()
        return _state_to_response(
            final_state,
            llm_calls=collector.all(),
            total_cost_usd=cost,
            total_prompt_tokens=prompt_tok,
            total_completion_tokens=completion_tok,
        )

    async def run_stream(self, request: ResearchRequest) -> AsyncGenerator[dict, None]:
        collector = LLMCallCollector()
        client = self._client_for_request(collector)
        graph = build_graph(client)
        final_state: ResearchState = {}
        stream_iter = graph.astream({"query": request.query}, stream_mode=["updates", "values"]).__aiter__()
        step_task: asyncio.Task | None = asyncio.create_task(anext(stream_iter))
        call_task: asyncio.Task | None = asyncio.create_task(collector.wait_next())
        graph_done = False

        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                while True:
                    wait_tasks = [t for t in (step_task, call_task) if t is not None]
                    if not wait_tasks:
                        break

                    done, _ = await asyncio.wait(wait_tasks, return_when=asyncio.FIRST_COMPLETED)

                    if call_task is not None and call_task in done:
                        item = call_task.result()
                        yield {"type": "llm_call", "payload": item.model_dump()}
                        call_task = asyncio.create_task(collector.wait_next())

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

                    if graph_done and collector.pending_count() == 0:
                        if call_task is not None:
                            call_task.cancel()
                            try:
                                await call_task
                            except asyncio.CancelledError:
                                pass
                            call_task = None
                        break
        except TimeoutError as exc:
            raise RuntimeError(f"[orchestrator] request timeout after {int(REQUEST_TIMEOUT_SECONDS)}s") from exc
        finally:
            for task in (step_task, call_task):
                if task is not None and not task.done():
                    task.cancel()
            for task in (step_task, call_task):
                if task is not None:
                    try:
                        await task
                    except (asyncio.CancelledError, StopAsyncIteration, Exception):
                        pass

        all_llm_calls = collector.all()
        cost, prompt_tok, completion_tok = collector.totals()
        response = _state_to_response(
            final_state,
            llm_calls=all_llm_calls,
            total_cost_usd=cost,
            total_prompt_tokens=prompt_tok,
            total_completion_tokens=completion_tok,
        )
        yield {"type": "final", "payload": response}


# ── helpers ────────────────────────────────────────────────────────────────


def _state_to_response(
    state: ResearchState,
    *,
    llm_calls: list[LLMCall] | None = None,
    total_cost_usd: float = 0.0,
    total_prompt_tokens: int = 0,
    total_completion_tokens: int = 0,
) -> ResearchResponse:
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
