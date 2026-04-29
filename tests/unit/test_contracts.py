"""Contract and registry consistency tests.

Verifies that AGENT_REGISTRY, NODE_CONTRACTS, and graph topology stay in sync.
"""

from __future__ import annotations

import pytest
from src.server.agents.registry import AGENT_REGISTRY
from src.server.utils.contract import NODE_CONTRACTS, ContractViolation, assert_reads, assert_writes

_GRAPH_NODES = {
    "planner",
    "research",
    "fundamental_analysis",
    "macro_analysis",
    "market_sentiment",
    "llm_judge",
    "scenario_scoring",
    "scenario_debate",
    "report_finalize",
}

_EXPECTED_PARALLEL_GROUPS: dict[str, set[str]] = {
    "analysis": {"fundamental_analysis", "macro_analysis", "market_sentiment"}
}


# ── Registry completeness ─────────────────────────────────────────────────


def test_registry_covers_all_graph_nodes():
    assert set(AGENT_REGISTRY.keys()) == _GRAPH_NODES


def test_node_contracts_mirror_registry():
    assert set(NODE_CONTRACTS.keys()) == set(AGENT_REGISTRY.keys())
    for agent_id, entry in AGENT_REGISTRY.items():
        contract = NODE_CONTRACTS[agent_id]
        assert contract.reads == entry.reads, f"{agent_id}: reads mismatch"
        assert contract.writes == entry.writes, f"{agent_id}: writes mismatch"


def test_failure_modes_are_valid():
    for agent_id, entry in AGENT_REGISTRY.items():
        assert entry.failure_mode in ("fail", "degrade"), f"{agent_id}: invalid failure_mode"


def test_capability_deps_only_on_data_fetching_nodes():
    pure_llm_nodes = {"planner", "llm_judge", "scenario_scoring", "scenario_debate", "report_finalize"}
    for agent_id in pure_llm_nodes:
        assert not AGENT_REGISTRY[agent_id].capability_deps, f"{agent_id} is pure LLM but declares capability_deps"


# ── Parallel group consistency ────────────────────────────────────────────


def test_parallel_groups_match_expected():
    by_group: dict[str, set[str]] = {}
    for agent_id, entry in AGENT_REGISTRY.items():
        if entry.parallel_group:
            by_group.setdefault(entry.parallel_group, set()).add(agent_id)
    assert by_group == _EXPECTED_PARALLEL_GROUPS


def test_parallel_group_nodes_are_degrade_mode():
    for agent_id, entry in AGENT_REGISTRY.items():
        if entry.parallel_group:
            assert entry.failure_mode == "degrade", f"{agent_id} in parallel group must be degradable"


# ── Data-flow topology ────────────────────────────────────────────────────


def test_no_node_reads_and_writes_same_field():
    skip = {"agent_statuses", "retry_questions", "research_iteration", "policy_decision"}
    for agent_id, entry in AGENT_REGISTRY.items():
        overlap = (entry.reads & entry.writes) - skip
        assert not overlap, f"{agent_id} reads and writes same fields: {overlap}"


def test_research_writes_feed_all_analysis_nodes():
    for node in ("fundamental_analysis", "macro_analysis", "market_sentiment"):
        assert "evidence" in AGENT_REGISTRY["research"].writes
        assert "evidence" in AGENT_REGISTRY[node].reads


def test_scoring_output_feeds_debate():
    assert "scenarios" in AGENT_REGISTRY["scenario_scoring"].writes
    assert "scenarios" in AGENT_REGISTRY["scenario_debate"].reads


def test_debate_output_feeds_report():
    assert "scenario_debate" in AGENT_REGISTRY["scenario_debate"].writes
    assert "scenario_debate" in AGENT_REGISTRY["report_finalize"].reads


def test_plan_context_flows_to_downstream_nodes():
    assert "plan_context" in AGENT_REGISTRY["planner"].writes
    for node in ("research", "fundamental_analysis", "scenario_scoring", "report_finalize"):
        assert "plan_context" in AGENT_REGISTRY[node].reads


def test_depends_on_no_self_reference_and_known_agents():
    known = set(AGENT_REGISTRY.keys())
    for agent_id, entry in AGENT_REGISTRY.items():
        assert agent_id not in entry.depends_on
        for dep in entry.depends_on:
            assert dep in known, f"{agent_id} depends_on unknown '{dep}'"


# ── Contract enforcement ──────────────────────────────────────────────────


def _state(*keys: str) -> dict:
    return {k: f"__{k}__" for k in keys}


def test_all_nodes_have_contracts():
    assert set(NODE_CONTRACTS.keys()) == _GRAPH_NODES


@pytest.mark.parametrize("node", list(NODE_CONTRACTS.keys()))
def test_declared_reads_do_not_raise(node):
    contract = NODE_CONTRACTS[node]
    assert_reads(_state(*contract.reads), contract.reads, node)


@pytest.mark.parametrize("node", list(NODE_CONTRACTS.keys()))
def test_undeclared_read_raises(node):
    contract = NODE_CONTRACTS[node]
    with pytest.raises(ContractViolation, match="undeclared_field_xyz"):
        assert_reads(_state(*contract.reads, "undeclared_field_xyz"), contract.reads, node)


@pytest.mark.parametrize("node", list(NODE_CONTRACTS.keys()))
def test_declared_writes_do_not_raise(node):
    contract = NODE_CONTRACTS[node]
    assert_writes(_state(*contract.writes), contract.writes, node)


@pytest.mark.parametrize("node", list(NODE_CONTRACTS.keys()))
def test_undeclared_write_raises(node):
    contract = NODE_CONTRACTS[node]
    with pytest.raises(ContractViolation, match="surprise_write_xyz"):
        assert_writes(_state(*contract.writes, "surprise_write_xyz"), contract.writes, node)


def test_agent_statuses_always_allowed_in_writes():
    for node, contract in NODE_CONTRACTS.items():
        assert_writes(_state(*contract.writes, "agent_statuses"), contract.writes, node)
