"""Research node — collects evidence and normalises raw data into ResearchState."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime

from src.server.models.analysis import Conflict, MetricsBlock, NormalizedData
from src.server.models.evidence import Evidence
from src.server.models.state import ResearchState
from src.server.services.cache import Cache
from src.server.services.finance_data import FinanceDataClient
from src.server.services.web_research import WebResearchClient
from src.server.utils.status import update_status

logger = logging.getLogger(__name__)

_finance = FinanceDataClient()
_web = WebResearchClient()
_cache = Cache()

_FINANCE_TTL = 3600       # 1 hour — financial data changes infrequently
_WEB_TTL = 900            # 15 min — news is more time-sensitive


def _cache_key(prefix: str, *parts: str) -> str:
    payload = ":".join(parts)
    return f"{prefix}:{hashlib.sha256(payload.encode()).hexdigest()[:16]}"


def _detect_conflicts(evidence: list[Evidence]) -> list[dict]:
    """
    Detect factual conflicts across evidence items.
    Currently checks: duplicate topics with contradictory reliability signals.
    Returns a list of conflict descriptors for downstream agents to reason about.
    """
    conflicts: list[dict] = []

    # Group evidence by related_topics and flag cases where high/low reliability
    # items cover the same topic with different source_types
    topic_sources: dict[str, list[Evidence]] = {}
    for ev in evidence:
        for topic in ev.related_topics:
            topic_sources.setdefault(topic, []).append(ev)

    for topic, items in topic_sources.items():
        source_types = {ev.source_type for ev in items}
        reliabilities = {ev.reliability for ev in items}
        # A conflict worth flagging: same topic covered by both high and low
        # reliability sources (e.g. financial_api vs web report different things)
        if "high" in reliabilities and "low" in reliabilities and len(source_types) > 1:
            ids = [ev.id for ev in items]
            conflicts.append({
                "topic": topic,
                "type": "reliability_divergence",
                "evidence_ids": ids,
                "note": (
                    f"Topic '{topic}' covered by both high and low-reliability sources "
                    f"({', '.join(sorted(source_types))}). Downstream agents should "
                    f"prefer high-reliability evidence."
                ),
            })

    return conflicts


async def research_node(state: ResearchState) -> ResearchState:
    query = state["query"]
    intent = state.get("intent")
    open_questions: list[str] = state.get("open_questions") or []
    pass_id: int = state.get("research_pass", 0)
    statuses = list(state.get("agent_statuses") or [])

    retrieved_at = datetime.now(UTC).isoformat()
    id_offset = pass_id * 100

    ticker: str | None = intent.ticker if intent else None

    new_evidence: list[Evidence] = []
    metrics: dict = {}
    missing_fields: list[str] = []
    ev_id = id_offset + 1

    # ── Finance API data ───────────────────────────────────────────────────
    if ticker:

        # Company info
        try:
            _ck = _cache_key("info", ticker)
            info = _cache.get(_ck) or {}
            if not info:
                info = await asyncio.to_thread(_finance.get_info, ticker)
                if info:
                    _cache.set(_ck, info, ttl_seconds=_FINANCE_TTL)
            if info:
                new_evidence.append(Evidence(
                    id=f"ev_{ev_id:03d}",
                    source_type="financial_api",
                    title=f"{info.get('name', ticker)} — company profile",
                    url=f"https://finance.yahoo.com/quote/{ticker}",
                    retrieved_at=retrieved_at,
                    summary=(
                        f"{info.get('name', ticker)} ({info.get('sector', 'unknown sector')}) — "
                        f"market cap {info.get('market_cap', 'N/A')}, "
                        f"P/E {info.get('trailing_pe', 'N/A')}, "
                        f"EV/EBITDA {info.get('ev_to_ebitda', 'N/A')}. "
                        f"{info.get('description', '')[:300]}"
                    ),
                    reliability="high",
                    related_topics=["company_profile", "valuation", "sector"],
                ))
                ev_id += 1
        except Exception:
            logger.warning("get_info failed for %s", ticker, exc_info=True)

        # Financials
        try:
            _ck = _cache_key("financials", ticker)
            financials = _cache.get(_ck) or {}
            if not financials:
                financials = await asyncio.to_thread(_finance.get_financials, ticker)
                if financials:
                    _cache.set(_ck, financials, ttl_seconds=_FINANCE_TTL)
            if financials:
                ttm = financials.get("ttm", {})
                metrics = {
                    "ttm": ttm,
                    "three_year_avg": financials.get("three_year_avg", {}),
                    "latest_quarter": financials.get("latest_quarter", {}),
                }
                missing_fields = financials.get("missing_fields", [])

                rev = ttm.get("revenue")
                gm = ttm.get("gross_margin_pct")
                om = ttm.get("operating_margin_pct")
                nm = ttm.get("net_margin_pct")
                summary_parts = [f"{ticker} financials (TTM):"]
                if rev is not None:
                    summary_parts.append(f"revenue ${rev:,.0f}")
                if gm is not None:
                    summary_parts.append(f"gross margin {gm:.1f}%")
                if om is not None:
                    summary_parts.append(f"operating margin {om:.1f}%")
                if nm is not None:
                    summary_parts.append(f"net margin {nm:.1f}%")
                if missing_fields:
                    summary_parts.append(f"missing: {', '.join(missing_fields)}")

                new_evidence.append(Evidence(
                    id=f"ev_{ev_id:03d}",
                    source_type="financial_api",
                    title=f"{ticker} — financial statements",
                    url=f"https://finance.yahoo.com/quote/{ticker}/financials",
                    retrieved_at=retrieved_at,
                    summary=". ".join(summary_parts),
                    reliability="high",
                    related_topics=["revenue", "margin", "profitability"],
                ))
                ev_id += 1
        except Exception:
            logger.warning("get_financials failed for %s", ticker, exc_info=True)

        # Price history
        try:
            _ck = _cache_key("price", ticker)
            price = _cache.get(_ck) or {}
            if not price:
                price = await asyncio.to_thread(_finance.get_price_history, ticker)
                if price:
                    _cache.set(_ck, price, ttl_seconds=_FINANCE_TTL)
            if price:
                ret_1y = price.get("period_return_pct")
                ret_30d = price.get("return_30d_pct")
                vol = price.get("volatility_annualised_pct")
                hi52 = price.get("52w_high")
                lo52 = price.get("52w_low")
                parts = [f"{ticker} price history:"]
                if ret_1y is not None:
                    parts.append(f"1y return {ret_1y:.1f}%")
                if ret_30d is not None:
                    parts.append(f"30d return {ret_30d:.1f}%")
                if vol is not None:
                    parts.append(f"annualised vol {vol:.1f}%")
                if hi52 is not None and lo52 is not None:
                    parts.append(f"52w range ${lo52:.2f}–${hi52:.2f}")

                new_evidence.append(Evidence(
                    id=f"ev_{ev_id:03d}",
                    source_type="financial_api",
                    title=f"{ticker} — price history",
                    url=f"https://finance.yahoo.com/quote/{ticker}/history",
                    retrieved_at=retrieved_at,
                    summary=". ".join(parts),
                    reliability="high",
                    related_topics=["price", "returns", "volatility"],
                ))
                ev_id += 1
                # Store price data in metrics for downstream agents
                metrics["price_history"] = price
        except Exception:
            logger.warning("get_price_history failed for %s", ticker, exc_info=True)

        # News (yfinance)
        try:
            _ck = _cache_key("news", ticker)
            news_items = _cache.get(_ck)
            if news_items is None:
                news_items = await asyncio.to_thread(_finance.get_news, ticker)
                _cache.set(_ck, news_items, ttl_seconds=_WEB_TTL)
            for item in news_items[:5]:
                new_evidence.append(Evidence(
                    id=f"ev_{ev_id:03d}",
                    source_type="news",
                    title=item.get("title", "News item"),
                    url=item.get("url"),
                    published_at=item.get("published_at"),
                    retrieved_at=retrieved_at,
                    summary=item.get("title", ""),
                    reliability="medium",
                    related_topics=["news", ticker.lower()],
                ))
                ev_id += 1
        except Exception:
            logger.warning("get_news failed for %s", ticker, exc_info=True)

    # ── Web search (Tavily) — runs for any query, ticker or not ───────────
    web_query = (
        f"{intent.subjects[0] if intent and intent.subjects else query} "
        f"{open_questions[0] if open_questions else 'investment analysis'}"
    ).strip()
    try:
        _ck = _cache_key("web", web_query)
        web_results = _cache.get(_ck)
        if web_results is None:
            web_results = await asyncio.to_thread(_web.search, web_query, 5)
            _cache.set(_ck, web_results, ttl_seconds=_WEB_TTL)
        seen_urls = {ev.url for ev in new_evidence if ev.url}
        for item in web_results:
            url = item.get("url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            new_evidence.append(Evidence(
                id=f"ev_{ev_id:03d}",
                source_type="web",
                title=item.get("title", "Web result"),
                url=url or None,
                published_at=item.get("published_date"),
                retrieved_at=retrieved_at,
                summary=item.get("content", item.get("title", "")),
                reliability="medium",
                related_topics=["web"],
            ))
            ev_id += 1
    except Exception:
        logger.warning("web search failed for query: %s", web_query, exc_info=True)

    # ── Fallback if no ticker or all calls failed ──────────────────────────
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

    raw_conflicts = _detect_conflicts(new_evidence)
    conflicts = [Conflict.model_validate(c) for c in raw_conflicts]

    metrics_block = MetricsBlock(
        ttm=metrics.get("ttm", {}),
        three_year_avg=metrics.get("three_year_avg", {}),
        latest_quarter=metrics.get("latest_quarter", {}),
        price_history=metrics.get("price_history", {}),
    )

    normalized_data = NormalizedData(
        query=query,
        intent=intent.model_dump() if intent else {},
        metrics=metrics_block,
        missing_fields=missing_fields,
        conflicts=conflicts,
        open_question_context=open_questions,
        pass_id=pass_id,
    )

    if statuses:
        statuses = update_status(
            statuses, "research",
            lifecycle="active", phase="collecting_evidence", action="collecting evidence",
        )
        statuses = update_status(
            statuses, "research",
            lifecycle="standby", phase="collecting_evidence", action="evidence collected",
            details=[f"evidence={len(new_evidence)}", f"pass={pass_id}"],
            progress_hint=f"{len(new_evidence)} evidence",
        )
        statuses = update_status(
            statuses, "fundamental_analysis",
            lifecycle="active", phase="analyzing_fundamentals", action="analysing fundamentals",
        )
        statuses = update_status(
            statuses, "market_sentiment",
            lifecycle="active", phase="analyzing_sentiment", action="analysing sentiment",
        )

    return {
        "evidence": new_evidence,
        "normalized_data": normalized_data,
        "research_pass": pass_id + 1,
        "agent_statuses": statuses,
    }
