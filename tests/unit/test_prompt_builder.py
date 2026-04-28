"""Guardrails: prompt assembly must match pre-centralization f-string behavior."""

from __future__ import annotations

from src.server.prompts import build_prompt


def test_fundamental_analysis_user_preserves_leading_newline_in_schema() -> None:
    """Old _SCHEMA used opening \"\"\"\\nReturn... — full .strip() dropped that leading newline."""
    system, user = build_prompt(
        "fundamental_analysis",
        "main",
        intent_str="Ticker: X | Scope: company | Horizon: 1y",
        focus_str="- margin",
        metrics_str="revenue",
        notes_str="- none",
        ev_lines="none",
        metrics_json="{}",
        supplemental_lines="none",
    )
    assert "senior equity analyst" in system
    assert user.startswith("\nReturn exactly this JSON structure")


def test_parse_intent_schema_same_shape() -> None:
    _, user = build_prompt("parse_intent", "main", query="Is NVDA overvalued?")
    assert user.startswith("\nReturn exactly this JSON structure")
    assert user.endswith("User query: Is NVDA overvalued?")


def test_research_query_planner_schema_has_no_leading_newline() -> None:
    """Old _QUERY_PLANNER_SCHEMA opened with \"\"\"Return (no blank line)."""
    _, user = build_prompt(
        "research",
        "query_planner",
        subject="NVDA",
        focus_lines="- growth",
        metrics="revenue",
        retry_q="none",
        existing="none",
    )
    assert user.startswith("Return exactly this JSON (no extra keys):")
    assert "SUBJECT: NVDA" in user


def test_scenario_debate_arbitrator_block_order() -> None:
    """Matches old: context, then advocacy blocks, then closing line."""
    _, user = build_prompt(
        "scenario_debate",
        "arbitrator",
        context="CTX",
        advocacy_blocks="BLOCK_A\n\nBLOCK_B",
    )
    assert user.index("CTX") < user.index("BLOCK_A")
    assert user.index("BLOCK_A") < user.index("BLOCK_B")
    assert user.rstrip().endswith(
        "Arbitrate: weigh the evidence quality behind each advocacy and "
        "produce final probabilities."
    )
