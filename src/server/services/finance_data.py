"""
Finance data service — wraps yfinance to produce normalised dicts that the
research node can directly write into ResearchState.normalized_data.

All public methods return plain dicts (no pandas / numpy objects) so results
are JSON-serialisable without extra conversion.  Missing values are `None`
rather than `NaN` so downstream code can use a simple truthiness check.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# yfinance is imported lazily inside each method so that unit tests can patch
# it without affecting module-level import order.


def _safe(value: Any) -> Any:
    """Convert numpy scalars / NaN to plain Python types; coerce NaN → None."""
    try:
        import math
        import numpy as np  # type: ignore[import]
        if isinstance(value, (np.integer,)):
            return int(value)
        if isinstance(value, (np.floating,)):
            v = float(value)
            return None if math.isnan(v) else v
        if isinstance(value, float) and math.isnan(value):
            return None
    except ImportError:
        pass
    return value


def _row(df, name: str, col_pos: int = 0) -> float | None:
    """Extract a single scalar from a yfinance DataFrame row, safely."""
    try:
        if name in df.index:
            val = df.loc[name].iloc[col_pos]
            return _safe(val)
    except Exception:
        pass
    return None


def _pct(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round(numerator / denominator * 100, 2)


def _growth(current: float | None, prior: float | None) -> float | None:
    if current is None or prior is None or prior == 0:
        return None
    return round((current - prior) / abs(prior) * 100, 2)


class FinanceDataClient:
    # ── public API ─────────────────────────────────────────────────────────

    def get_info(self, ticker: str) -> dict[str, Any]:
        """
        Returns company profile and key valuation/rating fields.
        Returns {} if the ticker is not found.
        """
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            info = t.info
            if not info.get("shortName"):
                logger.warning("ticker %s not found (no shortName)", ticker)
                return {}
            return {
                "ticker": ticker,
                "name": info.get("shortName"),
                "sector": info.get("sector"),
                "industry": info.get("industry"),
                "description": (info.get("longBusinessSummary") or "")[:500],
                "market_cap": _safe(info.get("marketCap")),
                "current_price": _safe(info.get("currentPrice")),
                "fifty_two_week_high": _safe(info.get("fiftyTwoWeekHigh")),
                "fifty_two_week_low": _safe(info.get("fiftyTwoWeekLow")),
                "trailing_pe": _safe(info.get("trailingPE")),
                "forward_pe": _safe(info.get("forwardPE")),
                "price_to_book": _safe(info.get("priceToBook")),
                "ev_to_ebitda": _safe(info.get("enterpriseToEbitda")),
                "trailing_eps": _safe(info.get("trailingEps")),
                "forward_eps": _safe(info.get("forwardEps")),
                "revenue_growth_yoy": _safe(info.get("revenueGrowth")),  # decimal, e.g. 0.73
                "gross_margins": _safe(info.get("grossMargins")),
                "operating_margins": _safe(info.get("operatingMargins")),
                "profit_margins": _safe(info.get("profitMargins")),
                "recommendation": info.get("recommendationKey"),
                "analyst_count": _safe(info.get("numberOfAnalystOpinions")),
            }
        except Exception as exc:
            logger.error("get_info(%s) failed: %s", ticker, exc)
            return {}

    def get_financials(self, ticker: str) -> dict[str, Any]:
        """
        Returns normalised financial metrics across three time slices:
          - ttm / latest annual (most recent income statement column)
          - prior_year (one year back)
          - latest_quarter (most recent quarterly column)

        All monetary values in USD. Percentages as floats (e.g. 71.07 = 71.07%).
        Missing values are None; all missing field names collected in missing_fields[].
        """
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            inc = t.income_stmt          # annual, newest column first
            q_inc = t.quarterly_income_stmt
            cf = t.cashflow
            bs = t.balance_sheet

            # ── annual metrics ──────────────────────────────────────────
            rev_latest  = _row(inc, "Total Revenue", 0)
            rev_prior   = _row(inc, "Total Revenue", 1)
            gp_latest   = _row(inc, "Gross Profit", 0)
            op_latest   = _row(inc, "Operating Income", 0)
            ni_latest   = _row(inc, "Net Income", 0)
            eps_latest  = _row(inc, "Diluted EPS", 0)

            rev_prior2  = _row(inc, "Total Revenue", 2)  # for 3y avg
            gp_prior    = _row(inc, "Gross Profit", 1)
            op_prior    = _row(inc, "Operating Income", 1)

            fcf_latest  = _row(cf, "Free Cash Flow", 0)
            capex       = _row(cf, "Capital Expenditure", 0)
            total_debt  = _row(bs, "Total Debt", 0)

            # ── quarterly metrics ───────────────────────────────────────
            q_rev       = _row(q_inc, "Total Revenue", 0)
            q_gp        = _row(q_inc, "Gross Profit", 0)
            q_op        = _row(q_inc, "Operating Income", 0)
            q_rev_prior = _row(q_inc, "Total Revenue", 4)   # same Q prior year

            # ── derived metrics ─────────────────────────────────────────
            ttm = {
                "revenue":              rev_latest,
                "revenue_growth_yoy_pct": _growth(rev_latest, rev_prior),
                "gross_margin_pct":     _pct(gp_latest, rev_latest),
                "operating_margin_pct": _pct(op_latest, rev_latest),
                "net_margin_pct":       _pct(ni_latest, rev_latest),
                "diluted_eps":          eps_latest,
                "free_cash_flow":       fcf_latest,
                "capex":                capex,
                "total_debt":           total_debt,
            }

            # 3-year compound revenue growth (latest vs 2 years ago)
            cagr_3y = None
            if rev_latest and rev_prior2 and rev_prior2 > 0:
                cagr_3y = round(((rev_latest / rev_prior2) ** 0.5 - 1) * 100, 2)

            # 3-year average operating margin
            op_margins = [
                _pct(op_latest, rev_latest),
                _pct(op_prior,  rev_prior),
            ]
            op_margins_clean = [x for x in op_margins if x is not None]
            avg_op_margin = round(sum(op_margins_clean) / len(op_margins_clean), 2) if op_margins_clean else None

            three_year_avg = {
                "revenue_cagr_pct":       cagr_3y,
                "avg_operating_margin_pct": avg_op_margin,
            }

            latest_quarter = {
                "revenue":              q_rev,
                "revenue_growth_yoy_pct": _growth(q_rev, q_rev_prior),
                "gross_margin_pct":     _pct(q_gp, q_rev),
                "operating_margin_pct": _pct(q_op, q_rev),
            }

            # ── missing fields ──────────────────────────────────────────
            required = {
                "ttm.revenue": ttm["revenue"],
                "ttm.gross_margin_pct": ttm["gross_margin_pct"],
                "ttm.operating_margin_pct": ttm["operating_margin_pct"],
                "ttm.free_cash_flow": ttm["free_cash_flow"],
                "latest_quarter.revenue": latest_quarter["revenue"],
            }
            missing_fields = [k for k, v in required.items() if v is None]

            return {
                "ttm": ttm,
                "three_year_avg": three_year_avg,
                "latest_quarter": latest_quarter,
                "missing_fields": missing_fields,
                "retrieved_at": datetime.now(UTC).isoformat(),
            }

        except Exception as exc:
            logger.error("get_financials(%s) failed: %s", ticker, exc)
            return {
                "ttm": {}, "three_year_avg": {}, "latest_quarter": {},
                "missing_fields": ["all — data unavailable"],
                "retrieved_at": datetime.now(UTC).isoformat(),
                "error": str(exc),
            }

    def get_price_history(self, ticker: str, period: str = "1y") -> dict[str, Any]:
        """
        Returns price summary statistics for the given period.
        period: yfinance period string, e.g. "1mo", "3mo", "6mo", "1y", "2y".
        """
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            hist = t.history(period=period)
            if hist.empty:
                return {"error": f"no price history for {ticker}"}

            close = hist["Close"]
            first, last = float(close.iloc[0]), float(close.iloc[-1])
            total_return_pct = round((last - first) / first * 100, 2)

            # 30-day return (or full period if shorter)
            close_30d = hist["Close"].iloc[-min(22, len(hist)):]
            ret_30d = round((float(close_30d.iloc[-1]) - float(close_30d.iloc[0])) / float(close_30d.iloc[0]) * 100, 2)

            daily_returns = close.pct_change().dropna()
            volatility_annualised_pct = round(float(daily_returns.std()) * (252 ** 0.5) * 100, 2)

            return {
                "ticker": ticker,
                "period": period,
                "start_price": round(first, 2),
                "end_price": round(last, 2),
                "period_return_pct": total_return_pct,
                "return_30d_pct": ret_30d,
                "volatility_annualised_pct": volatility_annualised_pct,
                "52w_high": round(float(hist["High"].max()), 2),
                "52w_low": round(float(hist["Low"].min()), 2),
                "retrieved_at": datetime.now(UTC).isoformat(),
            }
        except Exception as exc:
            logger.error("get_price_history(%s) failed: %s", ticker, exc)
            return {"error": str(exc)}

    def get_news(self, ticker: str) -> list[dict[str, Any]]:
        """
        Returns up to 10 recent news items for the ticker.
        Each item: { title, url, publisher, published_at, summary }
        """
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            raw_news = t.news or []
            results = []
            for item in raw_news[:10]:
                content = item.get("content") or {}
                title = content.get("title") or ""
                if not title:
                    continue
                canonical = content.get("canonicalUrl") or {}
                url = canonical.get("url") if isinstance(canonical, dict) else None
                provider = content.get("provider") or {}
                publisher = provider.get("displayName") if isinstance(provider, dict) else None
                published_at = content.get("pubDate") or content.get("displayTime")
                summary = content.get("summary") or content.get("description") or ""
                results.append({
                    "title": title,
                    "url": url,
                    "publisher": publisher,
                    "published_at": published_at,
                    "summary": summary[:300] if summary else "",
                })
            return results
        except Exception as exc:
            logger.error("get_news(%s) failed: %s", ticker, exc)
            return []
