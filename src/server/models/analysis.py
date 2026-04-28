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


class Valuation(BaseModel):
    relative_multiple_view: str


class FundamentalAnalysis(BaseModel):
    agent: str = "fundamental_analysis"
    claims: list[Claim] = Field(default_factory=list)
    business_quality: BusinessQuality = Field(
        default_factory=lambda: BusinessQuality(view="stable")
    )
    valuation: Valuation = Field(
        default_factory=lambda: Valuation(relative_multiple_view="unavailable")
    )
    fundamental_risks: list[Risk] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    degraded: bool = False

    model_config = {"populate_by_name": True}


# ── MarketSentiment ────────────────────────────────────────────────────────


class NewsSentiment(BaseModel):
    direction: Literal["positive", "neutral", "negative"]


class PriceAction(BaseModel):
    return_30d_pct: float | None = None
    volatility: Literal["high", "medium", "low"] = "medium"


class MarketNarrative(BaseModel):
    summary: str


class MarketSentiment(BaseModel):
    agent: str = "market_sentiment"
    claims: list[Claim] = Field(default_factory=list)
    news_sentiment: NewsSentiment = Field(
        default_factory=lambda: NewsSentiment(direction="neutral")
    )
    price_action: PriceAction | None = None
    market_narrative: MarketNarrative = Field(
        default_factory=lambda: MarketNarrative(
            summary="Sentiment analysis unavailable."
        )
    )
    sentiment_risks: list[Risk] = Field(default_factory=list)
    degraded: bool = False

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
    macro_view: str = "Macro analysis unavailable."
    macro_drivers: list[str] = Field(default_factory=list)
    macro_risks: list[MacroRisk] = Field(default_factory=list)
    rate_environment: Literal["tightening", "easing", "stable"] = "stable"
    growth_environment: Literal["expanding", "contracting", "stable"] = "stable"
    degraded: bool = False


# ── JudgeDecision ─────────────────────────────────────────────────────────


class JudgeDecision(BaseModel):
    should_retry: bool = False
    retry_question: str = ""
    reason: str = ""


# ── ScenarioDebate ─────────────────────────────────────────────────────────


class ScenarioAdvocacy(BaseModel):
    """Output from one scenario advocate in round 1."""

    scenario_name: str
    advocacy_thesis: str
    supporting_arguments: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    contested_scenarios: list[str] = Field(default_factory=list)


class ProbabilityAdjustment(BaseModel):
    scenario_name: str
    before: float = Field(..., ge=0, le=1)
    after: float = Field(..., ge=0, le=1)
    delta: float
    reason: str


class ScenarioDebate(BaseModel):
    debate_summary: str = "Debate unavailable."
    advocacy_summaries: list[dict[str, Any]] = Field(default_factory=list)
    probability_adjustments: list[ProbabilityAdjustment] = Field(default_factory=list)
    calibrated_scenarios: list[Any] = Field(
        default_factory=list
    )  # list[Scenario] — avoids circular import
    confidence: Literal["high", "medium", "low"] = "medium"
    debate_flags: list[str] = Field(default_factory=list)
    degraded: bool = False


# ── ReportPlan ────────────────────────────────────────────────────────────


class ReportSection(BaseModel):
    id: str
    title: str
    source: str  # which intermediate result fills this section
    required: bool = True


class ReportPlan(BaseModel):
    report_type: (
        str  # e.g. "valuation", "comparison", "risk_review", "scenario", "general"
    )
    sections: list[ReportSection]


class CustomSection(BaseModel):
    """A query-specific narrative section proposed by the planning agent."""

    id: str  # snake_case, unique, e.g. "valuation_deep_dive"
    title: str  # display title, e.g. "Valuation Deep-Dive"
    focus: str  # the specific question/angle LLM must answer in this section


class PlanContext(BaseModel):
    """Consolidated planning-agent output consumed by downstream nodes."""

    research_focus: list[str] = Field(default_factory=list)
    must_have_metrics: list[str] = Field(default_factory=list)
    plan_notes: list[str] = Field(default_factory=list)
    report_plan: ReportPlan
    custom_sections: list[CustomSection] = Field(default_factory=list)


# ── QualityMetrics ─────────────────────────────────────────────────────────


class QualityMetrics(BaseModel):
    citation_coverage: float = Field(default=0.0, ge=0, le=1)
    scenario_probability_valid: bool = False
    debate_applied: bool = False
    unresolved_issues: int = 0
    confidence: Literal["high", "medium", "low"] = "low"


# ── NormalizedData ─────────────────────────────────────────────────────────


class NormalizedData(BaseModel):
    query: str
    intent: dict[str, Any] = Field(default_factory=dict)
    metrics: MetricsBlock = Field(default_factory=MetricsBlock)
    missing_fields: list[str] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    open_question_context: list[str] = Field(default_factory=list)
    pass_id: int = 0
