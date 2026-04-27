"""Typed contracts for normalized finance service payloads."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CompanyInfo(BaseModel):
    ticker: str
    name: str
    sector: str | None = None
    industry: str | None = None
    description: str = ""
    market_cap: float | int | None = None
    current_price: float | int | None = None
    fifty_two_week_high: float | int | None = None
    fifty_two_week_low: float | int | None = None
    trailing_pe: float | int | None = None
    forward_pe: float | int | None = None
    price_to_book: float | int | None = None
    ev_to_ebitda: float | int | None = None
    trailing_eps: float | int | None = None
    forward_eps: float | int | None = None
    revenue_growth_yoy: float | int | None = None
    gross_margins: float | int | None = None
    operating_margins: float | int | None = None
    profit_margins: float | int | None = None
    recommendation: str | None = None
    analyst_count: float | int | None = None


class FinancialSlice(BaseModel):
    revenue: float | int | None = None
    revenue_growth_yoy_pct: float | int | None = None
    gross_margin_pct: float | int | None = None
    operating_margin_pct: float | int | None = None


class TTMFinancialSlice(FinancialSlice):
    net_margin_pct: float | int | None = None
    diluted_eps: float | int | None = None
    free_cash_flow: float | int | None = None
    capex: float | int | None = None
    total_debt: float | int | None = None


class ThreeYearAverages(BaseModel):
    revenue_cagr_pct: float | int | None = None
    avg_operating_margin_pct: float | int | None = None


class FinancialsPayload(BaseModel):
    ttm: TTMFinancialSlice = Field(default_factory=TTMFinancialSlice)
    three_year_avg: ThreeYearAverages = Field(default_factory=ThreeYearAverages)
    latest_quarter: FinancialSlice = Field(default_factory=FinancialSlice)
    missing_fields: list[str] = Field(default_factory=list)
    retrieved_at: str
    error: str | None = None


class PriceHistoryPayload(BaseModel):
    ticker: str | None = None
    period: str | None = None
    start_price: float | int | None = None
    end_price: float | int | None = None
    period_return_pct: float | int | None = None
    return_30d_pct: float | int | None = None
    volatility_annualised_pct: float | int | None = None
    high_52w: float | int | None = Field(default=None, alias="52w_high")
    low_52w: float | int | None = Field(default=None, alias="52w_low")
    retrieved_at: str | None = None
    error: str | None = None

    model_config = {"populate_by_name": True}


JSONDict = dict[str, Any]
