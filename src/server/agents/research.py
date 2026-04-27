"""Research node — collects evidence and normalises raw data into ResearchState."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from src.server.models.evidence import Evidence
from src.server.models.state import ResearchState
from src.server.services.finance_data import FinanceDataClient
from src.server.utils.status import update_status

logger = logging.getLogger(__name__)

_finance = FinanceDataClient()


def research_node(state: ResearchState) -> ResearchState:
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

    # ── Finance API data ───────────────────────────────────────────────────
    if ticker:
        ev_id = id_offset + 1

        # Company info
        try:
            info = _finance.get_info(ticker)
            if info:
                new_evidence.append(Evidence(
                    id=f"ev_{ev_id:03d}",
                    source_type="financial_api",
                    title=f"{info.get('name', ticker)} — company profile",
                    url=f"https://finance.yahoo.com/quote/{ticker}",
                    retrieved_at=retrieved_at,
                    summary=(
                        f"{info.get('name', ticker)} ({info.get('sector', 'unknown sector')}) — "
                        f"market cap {info.get('market_cap_fmt', 'N/A')}, "
                        f"P/E {info.get('pe_ratio', 'N/A')}, "
                        f"EV/EBITDA {info.get('ev_ebitda', 'N/A')}. "
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
            financials = _finance.get_financials(ticker)
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
                ni = ttm.get("net_income")
                summary_parts = [f"{ticker} financials (TTM):"]
                if rev is not None:
                    summary_parts.append(f"revenue ${rev:,.0f}")
                if gm is not None:
                    summary_parts.append(f"gross margin {gm:.1f}%")
                if om is not None:
                    summary_parts.append(f"operating margin {om:.1f}%")
                if ni is not None:
                    summary_parts.append(f"net income ${ni:,.0f}")
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
            price = _finance.get_price_history(ticker)
            if price:
                ret_1y = price.get("return_1y_pct")
                ret_30d = price.get("return_30d_pct")
                vol = price.get("annualised_volatility_pct")
                hi52 = price.get("high_52w")
                lo52 = price.get("low_52w")
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

        # News
        try:
            news_items = _finance.get_news(ticker)
            for i, item in enumerate(news_items[:5]):
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

    # ── Fallback if no ticker or all calls failed ──────────────────────────
    if not new_evidence:
        new_evidence.append(Evidence(
            id=f"ev_{id_offset + 1:03d}",
            source_type="web",
            title=f"General research — {query}",
            url=None,
            retrieved_at=retrieved_at,
            summary=f"No ticker identified; general research context for: {query}",
            reliability="low",
            related_topics=["general"],
        ))

    normalized_data: dict = {
        "query": query,
        "intent": intent.model_dump() if intent else {},
        "metrics": metrics,
        "missing_fields": missing_fields,
        "conflicts": [],
        "open_question_context": open_questions,
        "pass_id": pass_id,
    }

    if statuses:
        statuses = update_status(
            statuses, "research",
            status="completed", action="evidence collected",
            details=[f"evidence={len(new_evidence)}", f"pass={pass_id}"],
        )
        statuses = update_status(
            statuses, "fundamental_analysis",
            status="running", action="analysing fundamentals",
        )
        statuses = update_status(
            statuses, "market_sentiment",
            status="running", action="analysing sentiment",
        )

    return {
        "evidence": new_evidence,
        "normalized_data": normalized_data,
        "research_pass": pass_id + 1,
        "agent_statuses": statuses,
    }
