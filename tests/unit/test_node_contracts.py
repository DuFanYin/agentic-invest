"""Contract tests — verify each node's declared read/write boundaries.

CONTRACT_ENFORCE=1 must be set before importing contract.py (handled in
conftest.py via pytest's monkeypatch or by running with the env var).
The tests serve as a living spec of the data-flow topology.
"""

from __future__ import annotations

import pytest
from src.server.utils.contract import (
    NODE_CONTRACTS,
    ContractViolation,
    assert_reads,
    assert_writes,
)

# ── Helpers ────────────────────────────────────────────────────────────────


def _state(*keys: str) -> dict:
    """Build a minimal state dict with the given keys set to sentinel values."""
    return {k: f"__{k}__" for k in keys}


# ── Contract table completeness ────────────────────────────────────────────


def test_all_nodes_have_contracts():
    expected = {
        "parse_intent",
        "research",
        "fundamental_analysis",
        "macro_analysis",
        "market_sentiment",
        "llm_judge",
        "scenario_scoring",
        "scenario_debate",
        "report_finalize",
    }
    assert set(NODE_CONTRACTS.keys()) == expected


# ── assert_reads enforcement ───────────────────────────────────────────────


@pytest.mark.parametrize("node", list(NODE_CONTRACTS.keys()))
def test_declared_reads_do_not_raise(node):
    contract = NODE_CONTRACTS[node]
    state = _state(*contract.reads)
    assert_reads(state, contract.reads, node)  # must not raise


@pytest.mark.parametrize("node", list(NODE_CONTRACTS.keys()))
def test_undeclared_read_raises(node):
    contract = NODE_CONTRACTS[node]
    state = _state(*contract.reads, "undeclared_field_xyz")
    with pytest.raises(ContractViolation, match="undeclared_field_xyz"):
        assert_reads(state, contract.reads, node)


def test_global_reads_always_allowed():
    # query and agent_statuses are implicitly allowed in every node
    state = _state("query", "agent_statuses", "evidence")
    contract = NODE_CONTRACTS["macro_analysis"]  # only declares evidence + intent
    # agent_statuses is global — should not raise even though not in declared reads
    assert_reads(state, contract.reads | {"evidence"}, "macro_analysis")


# ── assert_writes enforcement ──────────────────────────────────────────────


@pytest.mark.parametrize("node", list(NODE_CONTRACTS.keys()))
def test_declared_writes_do_not_raise(node):
    contract = NODE_CONTRACTS[node]
    delta = _state(*contract.writes)
    assert_writes(delta, contract.writes, node)


@pytest.mark.parametrize("node", list(NODE_CONTRACTS.keys()))
def test_undeclared_write_raises(node):
    contract = NODE_CONTRACTS[node]
    # agent_statuses is a global write — use a truly undeclared field
    delta = _state(*contract.writes, "surprise_write_xyz")
    with pytest.raises(ContractViolation, match="surprise_write_xyz"):
        assert_writes(delta, contract.writes, node)


def test_agent_statuses_always_allowed_in_writes():
    # Every node may write agent_statuses without declaring it explicitly
    for node, contract in NODE_CONTRACTS.items():
        delta = _state(*contract.writes, "agent_statuses")
        assert_writes(delta, contract.writes, node)  # must not raise


# ── Topology sanity: write → read coverage ────────────────────────────────


def test_research_writes_are_readable_by_analysis_nodes():
    research_writes = NODE_CONTRACTS["research"].writes - {"agent_statuses"}
    fa_reads = NODE_CONTRACTS["fundamental_analysis"].reads
    macro_reads = NODE_CONTRACTS["macro_analysis"].reads
    ms_reads = NODE_CONTRACTS["market_sentiment"].reads
    # evidence is the key hand-off; all three analysis nodes must declare it
    assert "evidence" in research_writes
    assert "evidence" in fa_reads
    assert "evidence" in macro_reads
    assert "evidence" in ms_reads


def test_scoring_output_feeds_debate():
    assert "scenarios" in NODE_CONTRACTS["scenario_scoring"].writes
    assert "scenarios" in NODE_CONTRACTS["scenario_debate"].reads


def test_debate_output_feeds_report():
    assert "scenario_debate" in NODE_CONTRACTS["scenario_debate"].writes
    assert "scenario_debate" in NODE_CONTRACTS["report_finalize"].reads


def test_plan_context_feeds_research_and_analysis():
    assert "plan_context" in NODE_CONTRACTS["parse_intent"].writes
    assert "plan_context" in NODE_CONTRACTS["research"].reads
    assert "plan_context" in NODE_CONTRACTS["fundamental_analysis"].reads
    assert "plan_context" in NODE_CONTRACTS["scenario_scoring"].reads
    assert "plan_context" in NODE_CONTRACTS["report_finalize"].reads


def test_no_node_writes_to_its_own_inputs():
    # A node should not re-write a field it declared as a primary input.
    # Exclusions:
    #   agent_statuses — global write, every node updates it
    #   retry_questions — llm_judge and report_finalize legitimately rewrite it
    #   research_iteration — research increments its own counter each pass
    skip_fields = {
        "agent_statuses",
        "retry_questions",
        "research_iteration",
        "policy_decision",
    }
    for node, contract in NODE_CONTRACTS.items():
        overlap = (contract.reads & contract.writes) - skip_fields
        assert not overlap, f"{node} reads and writes same fields: {overlap}"
