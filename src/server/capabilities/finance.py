"""Finance capability — fetches company data and assembles Evidence items.

Wraps FinanceDataClient: info, financials, price history, news.
Each sub-fetch is independent; a failure produces missing_fields but does not
abort the others.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from src.server.models.evidence import Evidence
from src.server.services.cache import Cache
from src.server.services.finance_data import FinanceDataClient

logger = logging.getLogger(__name__)

_FINANCE_TTL = 3600  # 1 hour


def _cache_key(prefix: str, *parts: str) -> str:
    import hashlib

    payload = ":".join(parts)
    return f"{prefix}:{hashlib.sha256(payload.encode()).hexdigest()[:16]}"


@dataclass
class FinanceFetchResult:
    evidence: list[Evidence]
    metrics: dict  # ttm, three_year_avg, latest_quarter, price_history
    missing_fields: list[str]
    next_ev_id: int


async def fetch_finance_evidence(
    ticker: str,
    *,
    ev_id_start: int,
    retrieved_at: str,
    cache: Cache,
    client: FinanceDataClient,
) -> FinanceFetchResult:
    evidence: list[Evidence] = []
    metrics: dict = {}
    missing_fields: list[str] = []
    ev_id = ev_id_start

    # ── Company info ──────────────────────────────────────────────────────
    try:
        ck = _cache_key("info", ticker)
        info = cache.get(ck) or {}
        if not info:
            info = await asyncio.to_thread(client.get_info, ticker)
            if info:
                cache.set(ck, info, ttl_seconds=_FINANCE_TTL)
        if info:
            evidence.append(
                Evidence(
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
                )
            )
            ev_id += 1
    except Exception:
        logger.warning("get_info failed for %s", ticker, exc_info=True)

    # ── Financials ────────────────────────────────────────────────────────
    try:
        ck = _cache_key("financials", ticker)
        financials = cache.get(ck) or {}
        if not financials:
            financials = await asyncio.to_thread(client.get_financials, ticker)
            if financials:
                cache.set(ck, financials, ttl_seconds=_FINANCE_TTL)
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

            evidence.append(
                Evidence(
                    id=f"ev_{ev_id:03d}",
                    source_type="financial_api",
                    title=f"{ticker} — financial statements",
                    url=f"https://finance.yahoo.com/quote/{ticker}/financials",
                    retrieved_at=retrieved_at,
                    summary=". ".join(summary_parts),
                    reliability="high",
                    related_topics=["revenue", "margin", "profitability"],
                )
            )
            ev_id += 1
    except Exception:
        logger.warning("get_financials failed for %s", ticker, exc_info=True)

    # ── Price history ─────────────────────────────────────────────────────
    try:
        ck = _cache_key("price", ticker)
        price = cache.get(ck) or {}
        if not price:
            price = await asyncio.to_thread(client.get_price_history, ticker)
            if price:
                cache.set(ck, price, ttl_seconds=_FINANCE_TTL)
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

            evidence.append(
                Evidence(
                    id=f"ev_{ev_id:03d}",
                    source_type="financial_api",
                    title=f"{ticker} — price history",
                    url=f"https://finance.yahoo.com/quote/{ticker}/history",
                    retrieved_at=retrieved_at,
                    summary=". ".join(parts),
                    reliability="high",
                    related_topics=["price", "returns", "volatility"],
                )
            )
            ev_id += 1
            metrics["price_history"] = price
    except Exception:
        logger.warning("get_price_history failed for %s", ticker, exc_info=True)

    # ── News ──────────────────────────────────────────────────────────────
    try:
        ck = _cache_key("news", ticker)
        news_items = cache.get(ck)
        if news_items is None:
            news_items = await asyncio.to_thread(client.get_news, ticker)
            cache.set(ck, news_items, ttl_seconds=_FINANCE_TTL)
        for item in news_items[:5]:
            evidence.append(
                Evidence(
                    id=f"ev_{ev_id:03d}",
                    source_type="news",
                    title=item.get("title", "News item"),
                    url=item.get("url"),
                    published_at=item.get("published_at"),
                    retrieved_at=retrieved_at,
                    summary=item.get("title", ""),
                    reliability="medium",
                    related_topics=["news", ticker.lower()],
                )
            )
            ev_id += 1
    except Exception:
        logger.warning("get_news failed for %s", ticker, exc_info=True)

    return FinanceFetchResult(
        evidence=evidence,
        metrics=metrics,
        missing_fields=missing_fields,
        next_ev_id=ev_id,
    )
