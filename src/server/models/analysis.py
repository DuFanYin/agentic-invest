"""Typed Pydantic models for inter-agent payloads.

These replace the dict[str, Any] contracts that previously existed only in
LLM prompts. Validation happens at parse time in each agent node, so schema
drift is caught immediately at the node that produced the bad output.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Shared sub-models ──────────────────────────────────────────────────────

class Claim(BaseModel):
    statement: str
    confidence: Literal["high", "medium", "low"]
    evidence_ids: list[str] = Field(..., min_length=1)


class Risk(BaseModel):
    name: str
    impact: Literal["high", "medium", "low"]
    signal: str
    evidence_ids: list[str] = Field(..., min_length=1)


# ── FundamentalAnalysis ────────────────────────────────────────────────────

class BusinessQuality(BaseModel):
    view: Literal["strong", "stable", "weak", "deteriorating"]
    drivers: list[str] = Field(default_factory=list)


class Financials(BaseModel):
    profitability_trend: str
    cash_flow_quality: str


class Valuation(BaseModel):
    relative_multiple_view: str
    simplified_dcf_view: str = ""


class FundamentalAnalysis(BaseModel):
    agent: str = "fundamental_analysis"
    claims: list[Claim] = Field(..., min_length=1)
    business_quality: BusinessQuality
    financials: Financials
    valuation: Valuation
    fundamental_risks: list[Risk] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    # metrics is passed through from research — not produced by the LLM
    metrics: dict[str, Any] = Field(default_factory=dict)
    _llm_used: bool = True

    model_config = {"populate_by_name": True}


# ── MarketSentiment ────────────────────────────────────────────────────────

class NewsSentiment(BaseModel):
    direction: Literal["positive", "neutral", "negative"]
    confidence: Literal["high", "medium", "low"]


class PriceAction(BaseModel):
    trend: str
    return_30d_pct: float | None = None
    volatility: Literal["high", "medium", "low"] = "medium"


class MarketNarrative(BaseModel):
    summary: str
    crowding_risk: Literal["high", "medium", "low"] = "low"


class MarketSentiment(BaseModel):
    agent: str = "market_sentiment"
    claims: list[Claim] = Field(default_factory=list)
    news_sentiment: NewsSentiment
    price_action: PriceAction | None = None
    market_narrative: MarketNarrative
    sentiment_risks: list[Risk] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    _llm_used: bool = True

    model_config = {"populate_by_name": True}


# ── NormalizedData ─────────────────────────────────────────────────────────

class MetricsBlock(BaseModel):
    ttm: dict[str, Any] = Field(default_factory=dict)
    three_year_avg: dict[str, Any] = Field(default_factory=dict)
    latest_quarter: dict[str, Any] = Field(default_factory=dict)
    price_history: dict[str, Any] = Field(default_factory=dict)


class Conflict(BaseModel):
    topic: str
    type: str
    evidence_ids: list[str] = Field(default_factory=list)
    note: str = ""


# ── MacroAnalysis ──────────────────────────────────────────────────────────

class MacroRisk(BaseModel):
    name: str
    impact: Literal["high", "medium", "low"]
    signal: str


class MacroAnalysis(BaseModel):
    agent: str = "macro_analysis"
    macro_view: str
    macro_drivers: list[str] = Field(default_factory=list)
    macro_risks: list[MacroRisk] = Field(default_factory=list)
    macro_signals: list[str] = Field(default_factory=list)
    rate_environment: Literal["tightening", "easing", "stable"] = "stable"
    growth_environment: Literal["expanding", "contracting", "stable"] = "stable"
    missing_fields: list[str] = Field(default_factory=list)


# ── ScenarioDebate ─────────────────────────────────────────────────────────

class ProbabilityAdjustment(BaseModel):
    scenario_name: str
    before: float = Field(..., ge=0, le=1)
    after: float = Field(..., ge=0, le=1)
    delta: float
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)


class ScenarioDebate(BaseModel):
    debate_summary: str
    probability_adjustments: list[ProbabilityAdjustment] = Field(default_factory=list)
    calibrated_scenarios: list[Any] = Field(default_factory=list)  # list[Scenario] — avoids circular import
    confidence: Literal["high", "medium", "low"] = "medium"
    debate_flags: list[str] = Field(default_factory=list)


# ── QualityMetrics ─────────────────────────────────────────────────────────

class QualityMetrics(BaseModel):
    citation_coverage: float = Field(default=0.0, ge=0, le=1)
    scenario_probability_valid: bool = False
    debate_applied: bool = False
    unresolved_issues: int = 0
    confidence: Literal["high", "medium", "low"] = "low"


# ── Budget ─────────────────────────────────────────────────────────────────

class Budget(BaseModel):
    pass_used: int = 0
    pass_limit: int = 2
    elapsed_ms: int = 0
    timeout_ms: int = 120000


# ── NormalizedData ─────────────────────────────────────────────────────────

class NormalizedData(BaseModel):
    query: str
    intent: dict[str, Any] = Field(default_factory=dict)
    metrics: MetricsBlock = Field(default_factory=MetricsBlock)
    missing_fields: list[str] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    open_question_context: list[str] = Field(default_factory=list)
    pass_id: int = 0
