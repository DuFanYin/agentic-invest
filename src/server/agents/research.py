"""Research node — thin coordinator over capability layer.

Calls fetch_finance, fetch_macro, fetch_web, then normalize_evidence.
No evidence-assembly or conflict-detection logic lives here.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from src.server.capabilities.finance import fetch_finance_evidence
from src.server.capabilities.macro import fetch_macro_evidence
from src.server.capabilities.normalize import normalize_evidence
from src.server.capabilities.web import fetch_web_evidence
from src.server.config import CACHE_DB_PATH
from src.server.models.state import ResearchState
from src.server.services.cache import Cache
from src.server.services.finance_data import FinanceDataClient
from src.server.services.macro_data import MacroDataClient
from src.server.services.web_research import WebResearchClient
from src.server.utils.contract import NODE_CONTRACTS, assert_reads, assert_writes
from src.server.utils.status import update_status

_READS  = NODE_CONTRACTS["research"].reads
_WRITES = NODE_CONTRACTS["research"].writes

logger = logging.getLogger(__name__)

_finance = FinanceDataClient()
_macro   = MacroDataClient()
_web     = WebResearchClient()
_cache   = Cache(db_path=CACHE_DB_PATH)


async def research_node(state: ResearchState) -> ResearchState:
    assert_reads(state, _READS, "research")

    query           = state["query"]
    intent          = state.get("intent")
    plan_ctx        = state.get("plan_context")
    research_focus  = plan_ctx.research_focus if plan_ctx else []
    retry_questions = state.get("retry_questions") or []
    iteration_id    = state.get("research_iteration", 0)
    statuses        = list(state.get("agent_statuses") or [])

    retrieved_at = datetime.now(UTC).isoformat()
    ticker       = intent.ticker if intent else None
    ev_id        = iteration_id * 100 + 1

    new_evidence: list = []
    all_metrics:  dict = {}
    all_missing:  list[str] = []

    # ── Finance (ticker-gated) ────────────────────────────────────────────
    if ticker:
        fin = await fetch_finance_evidence(
            ticker,
            ev_id_start=ev_id,
            retrieved_at=retrieved_at,
            cache=_cache,
            client=_finance,
        )
        new_evidence.extend(fin.evidence)
        all_metrics.update(fin.metrics)
        all_missing.extend(fin.missing_fields)
        ev_id = fin.next_ev_id

    # ── Macro (always) ────────────────────────────────────────────────────
    mac = await fetch_macro_evidence(
        ev_id_start=ev_id,
        retrieved_at=retrieved_at,
        client=_macro,
    )
    new_evidence.extend(mac.evidence)
    ev_id = mac.next_ev_id

    # ── Web search (always) ───────────────────────────────────────────────
    subject = intent.subjects[0] if intent and intent.subjects else query
    if retry_questions:
        web_query = f"{subject} {retry_questions[0]}"
    elif research_focus:
        web_query = f"{subject} {research_focus[0]}"
    else:
        web_query = f"{subject} investment analysis"
    web_query = web_query.strip()

    seen_urls = {ev.url for ev in new_evidence if ev.url}
    web = await fetch_web_evidence(
        web_query,
        ev_id_start=ev_id,
        retrieved_at=retrieved_at,
        seen_urls=seen_urls,
        cache=_cache,
        client=_web,
    )
    new_evidence.extend(web.evidence)

    # ── Fail if nothing collected ─────────────────────────────────────────
    if not new_evidence:
        msg = "[research] no usable evidence collected from finance/news/web sources"
        logger.error(msg)
        if statuses:
            statuses = update_status(
                statuses, "research",
                lifecycle="failed", phase="collecting_evidence",
                action="evidence collection failed", last_error=msg,
            )
        raise RuntimeError(msg)

    # ── Normalize ─────────────────────────────────────────────────────────
    all_evidence   = (state.get("evidence") or []) + new_evidence
    normalized_data = normalize_evidence(
        query, intent, all_evidence, all_metrics, all_missing, retry_questions, iteration_id,
    )

    # ── Status updates ────────────────────────────────────────────────────
    if statuses:
        statuses = update_status(
            statuses, "research",
            lifecycle="standby", phase="collecting_evidence", action="evidence collected",
            details=[f"evidence={len(new_evidence)}", f"iteration={iteration_id}"],
            progress_hint=f"{len(new_evidence)} evidence",
        )
        statuses = update_status(
            statuses, "fundamental_analysis",
            lifecycle="active", phase="analyzing_fundamentals", action="analysing fundamentals",
        )
        statuses = update_status(
            statuses, "macro_analysis",
            lifecycle="active", phase="analyzing_macro", action="analysing macro environment",
        )
        statuses = update_status(
            statuses, "market_sentiment",
            lifecycle="active", phase="analyzing_sentiment", action="analysing sentiment",
        )

    delta = {
        "evidence": new_evidence,
        "normalized_data": normalized_data,
        "research_iteration": iteration_id + 1,
        "agent_statuses": statuses,
    }
    assert_writes(delta, _WRITES, "research")
    return delta
