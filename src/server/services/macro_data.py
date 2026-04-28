"""MacroDataClient — fetches FRED economic indicators and yfinance macro market signals."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from src.server.config import CACHE_DB_PATH
from src.server.config import FRED_API_KEY
from src.server.services.cache import Cache
from src.server.services.retry import (
    DEFAULT_FETCH_TIMEOUT_SECONDS,
    RETRYABLE_HTTP_STATUS,
    RetryableFetchError,
    retry_sync,
)

logger = logging.getLogger(__name__)

_cache = Cache(db_path=CACHE_DB_PATH)
_FRED_TTL = 6 * 3600  # 6 hours — economic indicators don't change intra-day
_MARKET_TTL = 15 * 60  # 15 minutes — VIX/yields move throughout the day

FRED_SERIES: dict[str, str] = {
    "FEDFUNDS": "Federal Funds Rate",
    "DGS10": "10-Year Treasury Yield",
    "T10Y2Y": "Yield Curve Spread (10y-2y)",
    "BAMLH0A0HYM2": "High Yield Credit Spread",
    "CPIAUCSL": "CPI (YoY)",
    "UNRATE": "Unemployment Rate",
    "GDPC1": "Real GDP (Quarterly)",
}

MACRO_TICKERS: dict[str, str] = {
    "^VIX": "VIX Fear Index",
    "^TNX": "10-Year Treasury Yield (market)",
    "DX-Y.NYB": "US Dollar Index",
}


def _fetch_fred_series(series_id: str, label: str, api_key: str) -> dict[str, Any]:
    import httpx

    try:
        url = (
            f"https://api.stlouisfed.org/fred/series/observations"
            f"?series_id={series_id}&api_key={api_key}&file_type=json"
            f"&observation_start={(datetime.now(UTC) - timedelta(days=400)).strftime('%Y-%m-%d')}"
            f"&sort_order=desc&limit=13"
        )

        def _request() -> httpx.Response:
            try:
                resp = httpx.get(url, timeout=DEFAULT_FETCH_TIMEOUT_SECONDS)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise RetryableFetchError(str(exc)) from exc
            if resp.status_code in RETRYABLE_HTTP_STATUS:
                raise RetryableFetchError(f"http {resp.status_code}")
            return resp

        resp = retry_sync(_request, op_name=f"fred.{series_id}")
        resp.raise_for_status()
        observations = resp.json().get("observations", [])
        # Filter out missing values
        valid = [o for o in observations if o.get("value") not in (".", "", None)]
        if not valid:
            return {
                "series_id": series_id,
                "label": label,
                "value": None,
                "direction": "unknown",
            }
        latest = valid[0]
        prev = valid[1] if len(valid) > 1 else None
        value = float(latest["value"])
        direction = "stable"
        if prev:
            prev_val = float(prev["value"])
            if value > prev_val + 0.01:
                direction = "rising"
            elif value < prev_val - 0.01:
                direction = "falling"
        return {
            "series_id": series_id,
            "label": label,
            "value": round(value, 4),
            "date": latest.get("date", ""),
            "direction": direction,
        }
    except Exception as exc:
        logger.warning("FRED fetch failed for %s: %s", series_id, exc)
        return {
            "series_id": series_id,
            "label": label,
            "value": None,
            "direction": "unknown",
        }


def _fetch_yf_macro(ticker: str, label: str) -> dict[str, Any]:
    try:
        import yfinance as yf  # type: ignore[import]

        tk = yf.Ticker(ticker)
        hist = retry_sync(
            lambda: tk.history(period="5d", interval="1d"),
            retry_on=(Exception,),
            op_name=f"yfinance.macro.{ticker}",
        )
        if hist.empty:
            return {
                "ticker": ticker,
                "label": label,
                "value": None,
                "direction": "unknown",
            }
        closes = hist["Close"].dropna()
        if closes.empty:
            return {
                "ticker": ticker,
                "label": label,
                "value": None,
                "direction": "unknown",
            }
        latest = float(closes.iloc[-1])
        prev = float(closes.iloc[-2]) if len(closes) >= 2 else latest
        direction = "stable"
        change_pct = (latest - prev) / prev * 100 if prev else 0
        if change_pct > 0.5:
            direction = "rising"
        elif change_pct < -0.5:
            direction = "falling"
        return {
            "ticker": ticker,
            "label": label,
            "value": round(latest, 4),
            "change_pct": round(change_pct, 2),
            "direction": direction,
        }
    except Exception as exc:
        logger.warning("yfinance macro fetch failed for %s: %s", ticker, exc)
        return {"ticker": ticker, "label": label, "value": None, "direction": "unknown"}


class MacroDataClient:
    async def get_fred_indicators(self) -> dict[str, Any]:
        """Return latest value + direction for each FRED series. Cached 6h."""
        if not FRED_API_KEY:
            logger.warning("FRED_API_KEY not set — skipping FRED data")
            return {}
        cached = _cache.get("fred:indicators")
        if cached:
            return cached
        tasks = {
            series_id: asyncio.to_thread(
                _fetch_fred_series, series_id, label, FRED_API_KEY
            )
            for series_id, label in FRED_SERIES.items()
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        data: dict[str, Any] = {}
        for series_id, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                logger.warning("FRED gather error for %s: %s", series_id, result)
                data[series_id] = {
                    "series_id": series_id,
                    "value": None,
                    "direction": "unknown",
                }
            else:
                data[series_id] = result
        _cache.set("fred:indicators", data, ttl_seconds=_FRED_TTL)
        return data

    async def get_market_signals(self) -> dict[str, Any]:
        """Return recent price stats for macro yfinance tickers. Cached 15min."""
        cached = _cache.get("macro:market_signals")
        if cached:
            return cached
        tasks = {
            ticker: asyncio.to_thread(_fetch_yf_macro, ticker, label)
            for ticker, label in MACRO_TICKERS.items()
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        data: dict[str, Any] = {}
        for ticker, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                logger.warning("market signal gather error for %s: %s", ticker, result)
                data[ticker] = {"ticker": ticker, "value": None, "direction": "unknown"}
            else:
                data[ticker] = result
        _cache.set("macro:market_signals", data, ttl_seconds=_MARKET_TTL)
        return data

    async def get_all(self) -> dict[str, Any]:
        """Fetch FRED indicators and market signals concurrently."""
        fred, signals = await asyncio.gather(
            self.get_fred_indicators(),
            self.get_market_signals(),
        )
        return {"fred": fred, "market_signals": signals}
