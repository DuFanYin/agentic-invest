"""Unit tests for gate-context copy injected into FA / macro / sentiment prompts."""

from src.server.prompts import analysis_gate_context_for_prompt


def test_gate_initial_pass():
    s = analysis_gate_context_for_prompt(research_iteration=1, retry_questions=[], retry_reason="none")
    assert "initial" in s
    assert "supplemental" not in s.lower()


def test_gate_supplemental_iteration():
    s = analysis_gate_context_for_prompt(research_iteration=2, retry_questions=[], retry_reason="none")
    assert "supplemental" in s.lower()
    assert "**2**" in s


def test_gate_lists_directives_and_reason():
    s = analysis_gate_context_for_prompt(
        research_iteration=3, retry_questions=["Cross-check margin trend vs peers"], retry_reason="analysis_robustness"
    )
    assert "Cross-check margin trend" in s
    assert "analysis_robustness" in s
