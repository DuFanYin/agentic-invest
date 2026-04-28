"""Unit tests for the deterministic policy engine and its 6 rules."""

import pytest

from src.server.services.policy import PolicyInput, evaluate_policy


def _inp(**overrides) -> PolicyInput:
    base = dict(
        research_iteration=1,
        evidence_counts={"web": 3, "financial_api": 2},
        conflict_count=0,
        missing_field_count=0,
        fa_degraded=False,
        macro_degraded=False,
        ms_degraded=False,
        judge_reason="none",
        judge_retry_question="",
        max_iterations=2,
    )
    base.update(overrides)
    return PolicyInput(**base)


# ── rule_iteration_limit ──────────────────────────────────────────────────


def test_iteration_limit_fires_at_max():
    dec = evaluate_policy(
        _inp(
            research_iteration=2,
            max_iterations=2,
            judge_reason="structural",
            judge_retry_question="fix ticker",
        )
    )
    assert dec.action == "continue"
    assert dec.reason_code == "iteration_limit"


def test_iteration_limit_does_not_fire_below_max():
    dec = evaluate_policy(
        _inp(
            research_iteration=1,
            max_iterations=2,
            judge_reason="structural",
            judge_retry_question="fix ticker",
        )
    )
    # structural fires instead
    assert dec.action == "retry_full_research"
    assert dec.reason_code == "structural"


# ── rule_structural_no_ticker ─────────────────────────────────────────────


def test_structural_gap_triggers_full_retry():
    dec = evaluate_policy(
        _inp(judge_reason="structural", judge_retry_question="need ticker")
    )
    assert dec.action == "retry_full_research"
    assert dec.reason_code == "structural"
    assert dec.retry_question == "need ticker"


# ── rule_all_analyses_degraded ────────────────────────────────────────────


def test_all_degraded_halts_pipeline():
    dec = evaluate_policy(_inp(fa_degraded=True, macro_degraded=True, ms_degraded=True))
    assert dec.action == "halt_with_degraded_output"
    assert dec.reason_code == "all_degraded"


def test_partial_degraded_does_not_halt():
    dec = evaluate_policy(
        _inp(fa_degraded=True, macro_degraded=False, ms_degraded=False)
    )
    # falls through to default continue
    assert dec.action == "continue"


# ── rule_evidence_conflict ────────────────────────────────────────────────


def test_evidence_conflict_triggers_web_retry():
    dec = evaluate_policy(
        _inp(
            judge_reason="evidence_conflict",
            judge_retry_question="resolve EPS conflict",
        )
    )
    assert dec.action == "retry_capability_only"
    assert "cap.fetch_web" in dec.targets
    assert dec.retry_question == "resolve EPS conflict"


# ── rule_analysis_robustness ──────────────────────────────────────────────


def test_analysis_robustness_triggers_full_retry():
    dec = evaluate_policy(
        _inp(judge_reason="analysis_robustness", judge_retry_question="get margin data")
    )
    assert dec.action == "retry_full_research"
    assert dec.reason_code == "analysis_robustness"
    assert dec.retry_question == "get margin data"


# ── rule_default_continue ─────────────────────────────────────────────────


def test_default_continue_fires_when_no_issues():
    dec = evaluate_policy(_inp())
    assert dec.action == "continue"
    assert dec.reason_code == "default"


# ── priority ordering ─────────────────────────────────────────────────────


def test_iteration_limit_beats_structural():
    """Iteration limit must take priority over structural gap."""
    dec = evaluate_policy(
        _inp(
            research_iteration=2,
            max_iterations=2,
            judge_reason="structural",
            judge_retry_question="need ticker",
        )
    )
    assert dec.reason_code == "iteration_limit"


def test_all_degraded_beats_evidence_conflict():
    """halt_with_degraded_output (rule 3) beats evidence_conflict (rule 4)."""
    dec = evaluate_policy(
        _inp(
            fa_degraded=True,
            macro_degraded=True,
            ms_degraded=True,
            judge_reason="evidence_conflict",
            judge_retry_question="resolve conflict",
        )
    )
    assert dec.action == "halt_with_degraded_output"
