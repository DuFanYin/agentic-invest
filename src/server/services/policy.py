"""Unified policy module: models, rules, and engine."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class PolicyInput(BaseModel):
    """Snapshot extracted from ResearchState for rule evaluation."""

    research_iteration: int
    fa_degraded: bool
    macro_degraded: bool
    ms_degraded: bool
    judge_reason: str  # structural|analysis_robustness|evidence_conflict|none|judge_degraded|skipped_iteration_cap
    judge_retry_question: str
    max_iterations: int


class PolicyDecision(BaseModel):
    """Routing instruction produced by policy evaluation."""

    action: Literal["continue", "retry_full_research", "retry_capability_only", "halt_with_degraded_output"]
    targets: list[str]
    retry_question: str
    reason_code: str
    rationale: str


def rule_iteration_limit(inp: PolicyInput) -> PolicyDecision | None:
    if inp.research_iteration >= inp.max_iterations:
        return PolicyDecision(
            action="continue",
            targets=[],
            retry_question="",
            reason_code="iteration_limit",
            rationale=f"max iterations ({inp.max_iterations}) reached - proceeding",
        )
    return None


def rule_structural_no_ticker(inp: PolicyInput) -> PolicyDecision | None:
    if inp.judge_reason == "structural":
        return PolicyDecision(
            action="retry_full_research",
            targets=[],
            retry_question=inp.judge_retry_question,
            reason_code="structural",
            rationale="company scope missing ticker - full research needed",
        )
    return None


def rule_all_analyses_degraded(inp: PolicyInput) -> PolicyDecision | None:
    if inp.fa_degraded and inp.macro_degraded and inp.ms_degraded:
        return PolicyDecision(
            action="halt_with_degraded_output",
            targets=[],
            retry_question="",
            reason_code="all_degraded",
            rationale="all three analysis nodes degraded - cannot generate report",
        )
    return None


def rule_evidence_conflict(inp: PolicyInput) -> PolicyDecision | None:
    if inp.judge_reason == "evidence_conflict" and inp.judge_retry_question:
        return PolicyDecision(
            action="retry_capability_only",
            targets=["cap.fetch_web"],
            retry_question=inp.judge_retry_question,
            reason_code="evidence_conflict",
            rationale="conflict likely resolvable with targeted web search",
        )
    return None


def rule_analysis_robustness(inp: PolicyInput) -> PolicyDecision | None:
    if inp.judge_reason == "analysis_robustness" and inp.judge_retry_question:
        return PolicyDecision(
            action="retry_full_research",
            targets=[],
            retry_question=inp.judge_retry_question,
            reason_code="analysis_robustness",
            rationale="analyses lack sufficient evidence - full research pass needed",
        )
    return None


def rule_default_continue(_inp: PolicyInput) -> PolicyDecision | None:
    return PolicyDecision(
        action="continue",
        targets=[],
        retry_question="",
        reason_code="default",
        rationale="no retry condition met - proceeding to scenario scoring",
    )


_RULES = [
    rule_iteration_limit,
    rule_structural_no_ticker,
    rule_all_analyses_degraded,
    rule_evidence_conflict,
    rule_analysis_robustness,
    rule_default_continue,
]


def evaluate_policy(inp: PolicyInput) -> PolicyDecision:
    """Run rules in priority order and return first matching decision."""

    for rule in _RULES:
        decision = rule(inp)
        if decision is not None:
            return decision
    return PolicyDecision(
        action="continue",
        targets=[],
        retry_question="",
        reason_code="engine_fallback",
        rationale="engine fallback - no rule matched",
    )
