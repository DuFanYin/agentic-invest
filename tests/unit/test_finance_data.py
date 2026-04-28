"""Unit tests for FinanceDataClient — all yfinance calls mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from src.server.services.finance_data import FinanceDataClient

# ── helper builders ────────────────────────────────────────────────────────


def _make_income_stmt(
    rev=(215_938e6, 130_497e6, 60_922e6, 26_974e6),
    gp=(153_463e6, 97_858e6, 44_301e6, 15_356e6),
    op=(130_387e6, 81_453e6, 32_972e6, 5_577e6),
    ni=(120_067e6, 72_880e6, 29_760e6, 4_368e6),
    eps=(4.90, 2.94, 1.19, 0.174),
) -> pd.DataFrame:
    cols = pd.to_datetime(["2026-01-31", "2025-01-31", "2024-01-31", "2023-01-31"])
    return pd.DataFrame(
        {
            "Total Revenue": [np.float64(v) for v in rev],
            "Gross Profit": [np.float64(v) for v in gp],
            "Operating Income": [np.float64(v) for v in op],
            "Net Income": [np.float64(v) for v in ni],
            "Diluted EPS": [np.float64(v) for v in eps],
        },
        index=cols,
    ).T


def _make_cashflow(fcf=96_676e6, capex=-6_042e6) -> pd.DataFrame:
    cols = pd.to_datetime(["2026-01-31"])
    return pd.DataFrame(
        {
            "Free Cash Flow": [np.float64(fcf)],
            "Capital Expenditure": [np.float64(capex)],
        },
        index=cols,
    ).T


def _make_balance_sheet(total_debt=11_412e6) -> pd.DataFrame:
    cols = pd.to_datetime(["2026-01-31"])
    return pd.DataFrame({"Total Debt": [np.float64(total_debt)]}, index=cols).T


def _make_quarterly_income(
    q_rev=68_127e6, q_gp=51_093e6, q_op=44_299e6, q_rev_prior=35_082e6
) -> pd.DataFrame:
    # 5 quarters so col index 4 (same Q prior year) exists
    cols = pd.to_datetime(
        ["2026-01-31", "2025-10-31", "2025-07-31", "2025-04-30", "2025-01-31"]
    )
    rows = {
        "Total Revenue": [np.float64(q_rev), 0, 0, 0, np.float64(q_rev_prior)],
        "Gross Profit": [np.float64(q_gp), 0, 0, 0, 0],
        "Operating Income": [np.float64(q_op), 0, 0, 0, 0],
    }
    return pd.DataFrame(rows, index=cols).T


def _make_ticker_mock(info: dict | None = None, *, no_name: bool = False) -> MagicMock:
    mock = MagicMock()
    base_info = {
        "shortName": None if no_name else "NVIDIA Corporation",
        "sector": "Technology",
        "industry": "Semiconductors",
        "longBusinessSummary": "NVIDIA makes GPUs.",
        "marketCap": np.float64(5_061_759_467_520),
        "currentPrice": np.float64(208.26),
        "fiftyTwoWeekHigh": np.float64(212.19),
        "fiftyTwoWeekLow": np.float64(104.08),
        "trailingPE": np.float64(42.59),
        "forwardPE": np.float64(18.53),
        "priceToBook": np.float64(32.18),
        "enterpriseToEbitda": np.float64(37.60),
        "trailingEps": np.float64(4.89),
        "forwardEps": np.float64(11.24),
        "revenueGrowth": np.float64(0.732),
        "grossMargins": np.float64(0.7107),
        "operatingMargins": np.float64(0.6502),
        "profitMargins": np.float64(0.5560),
        "recommendationKey": "strong_buy",
        "numberOfAnalystOpinions": np.float64(56),
    }
    if info:
        base_info.update(info)
    mock.info = base_info
    mock.income_stmt = _make_income_stmt()
    mock.quarterly_income_stmt = _make_quarterly_income()
    mock.cashflow = _make_cashflow()
    mock.balance_sheet = _make_balance_sheet()
    mock.history.return_value = pd.DataFrame(
        {
            "Close": [100.0, 110.0, 120.0, 130.0, 140.0],
            "High": [105.0, 115.0, 125.0, 135.0, 145.0],
            "Low": [95.0, 105.0, 115.0, 125.0, 135.0],
        },
        index=pd.date_range("2025-01-01", periods=5, freq="D"),
    )
    mock.news = [
        {
            "id": "abc",
            "content": {
                "title": "NVDA beats estimates",
                "pubDate": "2026-04-01T10:00:00Z",
                "summary": "NVIDIA reported record revenue.",
                "canonicalUrl": {"url": "https://example.com/nvda-beats"},
                "provider": {"displayName": "Reuters"},
            },
        }
    ]
    return mock


# ── get_info ───────────────────────────────────────────────────────────────


def test_get_info_returns_profile_fields():
    client = FinanceDataClient()
    with patch("yfinance.Ticker", return_value=_make_ticker_mock()):
        info = client.get_info("NVDA")

    assert info["name"] == "NVIDIA Corporation"
    assert info["sector"] == "Technology"
    assert info["ticker"] == "NVDA"
    assert isinstance(info["market_cap"], (int, float))
    assert info["trailing_pe"] == pytest.approx(42.59, rel=1e-2)
    assert info["recommendation"] == "strong_buy"


# ── get_financials ─────────────────────────────────────────────────────────


def test_get_financials_core_metrics_are_consistent():
    client = FinanceDataClient()
    with patch("yfinance.Ticker", return_value=_make_ticker_mock()):
        fin = client.get_financials("NVDA")

    assert fin["ttm"]["revenue"] == pytest.approx(215_938e6, rel=1e-3)
    # 153_463 / 215_938 ≈ 71.07 %
    assert fin["ttm"]["gross_margin_pct"] == pytest.approx(71.07, rel=1e-2)
    # (215_938 - 130_497) / 130_497 ≈ 65.47 %
    assert fin["ttm"]["revenue_growth_yoy_pct"] == pytest.approx(65.47, rel=1e-2)
    # sqrt(215_938 / 60_922) - 1 ≈ 88.2 %
    assert fin["three_year_avg"]["revenue_cagr_pct"] == pytest.approx(88.2, rel=1e-1)
    assert fin["latest_quarter"]["revenue"] == pytest.approx(68_127e6, rel=1e-3)
    # (68127 - 35082) / 35082 ≈ 94.2 %
    assert fin["latest_quarter"]["revenue_growth_yoy_pct"] == pytest.approx(
        94.2, rel=1e-1
    )


def test_get_financials_marks_missing_when_revenue_nan():
    mock = _make_ticker_mock()
    # Replace Total Revenue row with NaN for all columns
    mock.income_stmt.loc["Total Revenue"] = np.float64(float("nan"))
    mock.quarterly_income_stmt.loc["Total Revenue"] = np.float64(float("nan"))

    client = FinanceDataClient()
    with patch("yfinance.Ticker", return_value=mock):
        fin = client.get_financials("NVDA")

    assert "ttm.revenue" in fin["missing_fields"]
    assert "latest_quarter.revenue" in fin["missing_fields"]


def test_get_financials_exception_returns_fallback():
    client = FinanceDataClient()
    with patch("yfinance.Ticker", side_effect=RuntimeError("timeout")):
        fin = client.get_financials("NVDA")

    assert isinstance(fin["ttm"], dict)
    assert fin["ttm"]["revenue"] is None
    assert fin["missing_fields"] == ["all — data unavailable"]
    assert "error" in fin


# ── get_price_history ──────────────────────────────────────────────────────


def test_get_price_history_return_calculation():
    client = FinanceDataClient()
    with patch("yfinance.Ticker", return_value=_make_ticker_mock()):
        hist = client.get_price_history("NVDA")

    # mock: close goes 100 → 140, so (140-100)/100 = 40%
    assert hist["period_return_pct"] == pytest.approx(40.0)
