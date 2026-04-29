"""Research node — tactical execution layer.

Responsibilities:
  1. LLM query planner: given plan_context + current gaps, generates 3-5 targeted
     web search queries (adaptive search).
  2. Calls fetch_finance, fetch_macro, fetch_web (concurrent multi-query), then
     normalize_evidence.

The planner runs on every iteration (first pass and retries alike).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from src.server.capabilities.finance import fetch_finance_evidence
from src.server.capabilities.macro import fetch_macro_evidence
from src.server.capabilities.normalize import normalize_evidence
from src.server.capabilities.web import fetch_web_evidence
from src.server.config import CACHE_DB_PATH
from src.server.models.state import ResearchState
from src.server.prompts import build_prompt
from src.server.services.cache import Cache
from src.server.services.finance_data import FinanceDataClient
from src.server.services.llm_provider import LLMClient
from src.server.services.macro_data import MacroDataClient
from src.server.services.web_research import WebResearchClient
from src.server.utils.contract import NODE_CONTRACTS, assert_reads, assert_writes
from src.server.utils.status import update_status

_READS = NODE_CONTRACTS["research"].reads
_WRITES = NODE_CONTRACTS["research"].writes

logger = logging.getLogger(__name__)


async def _plan_web_queries(
    subject: str,
    *,
    research_focus: list[str],
    must_have_metrics: list[str],
    retry_questions: list[str],
    existing_queries: list[str],
    llm: LLMClient,
) -> list[str]:
    """Call LLM to generate 3-5 targeted web search queries."""
    focus_lines = "\n".join(f"- {f}" for f in research_focus[:4]) or "none"
    metrics = ", ".join(must_have_metrics[:6]) or "none"
    retry_q = retry_questions[0] if retry_questions else "none"
    existing = ", ".join(existing_queries[:10]) or "none"

    system, prompt = build_prompt(
        "research",
        "query_planner",
        subject=subject,
        focus_lines=focus_lines,
        metrics=metrics,
        retry_q=retry_q,
        existing=existing,
    )
    try:
        raw = await llm.call_with_retry(prompt, system=system, node="research")
        parsed = json.loads(raw)
        queries = [q for q in (parsed.get("queries") or []) if isinstance(q, str) and q.strip()]
        if queries:
            return queries[:5]
    except Exception:
        logger.warning("research: query planner LLM failed, using fallback queries")

    # Fallback: build queries from available context
    fallback = []
    if retry_questions:
        fallback.append(f"{subject} {retry_questions[0]}")
    if research_focus:
        fallback.append(f"{subject} {research_focus[0]}")
    fallback.append(f"{subject} investment analysis latest")
    return fallback[:5]


async def _run_capabilities(
    *,
    ticker: str | None,
    subject: str,
    retry_scope: list[str] | None,
    retry_questions: list[str],
    research_focus: list[str],
    must_have_metrics: list[str],
    prior_evidence: list,
    iteration_id: int,
    retrieved_at: str,
    llm: LLMClient,
    cache: Cache,
    finance_client: FinanceDataClient,
    macro_client: MacroDataClient,
    web_client: WebResearchClient,
) -> tuple[list, dict, list[str]]:
    """Run enabled capabilities and return (new_evidence, metrics, missing_fields).

    retry_scope=None means all capabilities are enabled (full research pass).
    """

    def _cap_enabled(cap: str) -> bool:
        return retry_scope is None or cap in retry_scope

    ev_id = iteration_id * 100 + 1
    new_evidence: list = []
    all_metrics: dict = {}
    all_missing: list[str] = []

    if ticker and _cap_enabled("cap.fetch_finance"):
        fin = await fetch_finance_evidence(
            ticker, ev_id_start=ev_id, retrieved_at=retrieved_at, cache=cache, client=finance_client
        )
        new_evidence.extend(fin.evidence)
        all_metrics.update(fin.metrics)
        all_missing.extend(fin.missing_fields)
        ev_id = fin.next_ev_id

    if _cap_enabled("cap.fetch_macro"):
        mac = await fetch_macro_evidence(ev_id_start=ev_id, retrieved_at=retrieved_at, client=macro_client)
        new_evidence.extend(mac.evidence)
        ev_id = mac.next_ev_id

    if _cap_enabled("cap.fetch_web"):
        existing_queries = [ev.title for ev in prior_evidence if getattr(ev, "source_type", "") == "web"]
        web_queries = await _plan_web_queries(
            subject,
            research_focus=research_focus,
            must_have_metrics=must_have_metrics,
            retry_questions=retry_questions,
            existing_queries=existing_queries,
            llm=llm,
        )
        seen_urls = {ev.url for ev in new_evidence if ev.url}
        web = await fetch_web_evidence(
            web_queries,
            ev_id_start=ev_id,
            retrieved_at=retrieved_at,
            seen_urls=seen_urls,
            cache=cache,
            client=web_client,
        )
        new_evidence.extend(web.evidence)

    return new_evidence, all_metrics, all_missing


async def research_node(
    state: ResearchState,
    *,
    llm: LLMClient | None = None,
    cache: Cache | None = None,
    finance_client: FinanceDataClient | None = None,
    macro_client: MacroDataClient | None = None,
    web_client: WebResearchClient | None = None,
) -> ResearchState:
    assert_reads(state, _READS, "research")

    llm = llm or LLMClient()
    cache = cache or Cache(db_path=CACHE_DB_PATH)
    finance_client = finance_client or FinanceDataClient()
    macro_client = macro_client or MacroDataClient(cache=cache)
    web_client = web_client or WebResearchClient()

    query = state["query"]
    intent = state.get("intent")
    plan_ctx = state.get("plan_context")
    retry_questions = state.get("retry_questions") or []
    iteration_id = state.get("research_iteration", 0)
    statuses = list(state.get("agent_statuses") or [])

    ticker = intent.ticker if intent else None
    subject = (intent.subjects[0] if intent and intent.subjects else None) or query
    retrieved_at = datetime.now(UTC).isoformat()

    new_evidence, all_metrics, all_missing = await _run_capabilities(
        ticker=ticker,
        subject=subject,
        retry_scope=state.get("retry_scope"),
        retry_questions=retry_questions,
        research_focus=plan_ctx.research_focus if plan_ctx else [],
        must_have_metrics=plan_ctx.must_have_metrics if plan_ctx else [],
        prior_evidence=state.get("evidence") or [],
        iteration_id=iteration_id,
        retrieved_at=retrieved_at,
        llm=llm,
        cache=cache,
        finance_client=finance_client,
        macro_client=macro_client,
        web_client=web_client,
    )

    if not new_evidence:
        msg = "[research] no usable evidence collected from finance/news/web sources"
        logger.error(msg)
        if statuses:
            statuses = update_status(
                statuses,
                "research",
                lifecycle="failed",
                phase="collecting_evidence",
                action="evidence collection failed",
                last_error=msg,
            )
        raise RuntimeError(msg)

    all_evidence = (state.get("evidence") or []) + new_evidence
    normalized_data = normalize_evidence(
        query, intent, all_evidence, all_metrics, all_missing, retry_questions, iteration_id
    )

    if statuses:
        statuses = update_status(
            statuses,
            "research",
            lifecycle="standby",
            phase="collecting_evidence",
            action="evidence collected",
            details=[f"evidence={len(new_evidence)}", f"iteration={iteration_id}"],
            progress_hint=f"{len(new_evidence)} evidence",
        )
        statuses = update_status(
            statuses,
            "fundamental_analysis",
            lifecycle="active",
            phase="analyzing_fundamentals",
            action="analysing fundamentals",
        )
        statuses = update_status(
            statuses,
            "macro_analysis",
            lifecycle="active",
            phase="analyzing_macro",
            action="analysing macro environment",
        )
        statuses = update_status(
            statuses, "market_sentiment", lifecycle="active", phase="analyzing_sentiment", action="analysing sentiment"
        )

    delta = {
        "evidence": new_evidence,
        "normalized_data": normalized_data,
        "research_iteration": iteration_id + 1,
        "agent_statuses": statuses,
    }
    assert_writes(delta, _WRITES, "research")
    return delta
