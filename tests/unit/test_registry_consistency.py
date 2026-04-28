"""Registry consistency tests.

Verify that AGENT_REGISTRY, NODE_CONTRACTS, and the graph topology stay in sync.
These tests catch drift when an agent is added/removed without updating the registry.
"""

from __future__ import annotations

import pytest

from src.server.agents.registry import AGENT_REGISTRY, AgentRegistryEntry
from src.server.utils.contract import NODE_CONTRACTS, NodeContract

# All agents wired into the LangGraph graph (must match orchestrator.build_graph)
_GRAPH_NODES = {
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

# Known parallel groups and their expected members
_EXPECTED_PARALLEL_GROUPS: dict[str, set[str]] = {
    "analysis": {"fundamental_analysis", "macro_analysis", "market_sentiment"},
}


# ── Registry completeness ─────────────────────────────────────────────────


def test_registry_covers_all_graph_nodes():
    assert set(AGENT_REGISTRY.keys()) == _GRAPH_NODES, (
        f"Registry missing: {_GRAPH_NODES - set(AGENT_REGISTRY.keys())}, "
        f"extra: {set(AGENT_REGISTRY.keys()) - _GRAPH_NODES}"
    )


def test_node_contracts_derived_from_registry():
    """NODE_CONTRACTS must exactly mirror registry reads/writes."""
    assert set(NODE_CONTRACTS.keys()) == set(AGENT_REGISTRY.keys())
    for agent_id, entry in AGENT_REGISTRY.items():
        contract = NODE_CONTRACTS[agent_id]
        assert contract.reads == entry.reads, f"{agent_id}: reads mismatch"
        assert contract.writes == entry.writes, f"{agent_id}: writes mismatch"


# ── Failure mode validity ─────────────────────────────────────────────────


def test_failure_modes_are_valid():
    for agent_id, entry in AGENT_REGISTRY.items():
        assert entry.failure_mode in ("fail", "degrade"), (
            f"{agent_id}: invalid failure_mode '{entry.failure_mode}'"
        )


def test_capability_deps_only_on_nodes_that_use_capabilities():
    """Only nodes that actually call capabilities should declare capability_deps.
    Pure LLM-synthesis nodes (no data fetching) should have empty capability_deps."""
    pure_llm_nodes = {
        "parse_intent",
        "llm_judge",
        "scenario_scoring",
        "scenario_debate",
        "report_finalize",
    }
    for agent_id in pure_llm_nodes:
        entry = AGENT_REGISTRY[agent_id]
        assert not entry.capability_deps, (
            f"{agent_id} is a pure LLM node but declares capability_deps "
            f"{entry.capability_deps}."
        )


# ── Parallel group consistency ────────────────────────────────────────────


def test_parallel_groups_match_expected_members():
    by_group: dict[str, set[str]] = {}
    for agent_id, entry in AGENT_REGISTRY.items():
        if entry.parallel_group:
            by_group.setdefault(entry.parallel_group, set()).add(agent_id)
    assert by_group == _EXPECTED_PARALLEL_GROUPS, (
        f"Parallel groups differ. Got: {by_group}"
    )


def test_parallel_group_nodes_are_all_degrade_mode():
    """Nodes in a parallel group must be degrade-mode — a hard fail in one
    parallel branch kills the entire fan-in step."""
    for agent_id, entry in AGENT_REGISTRY.items():
        if entry.parallel_group:
            assert entry.failure_mode == "degrade", (
                f"{agent_id} is in parallel_group='{entry.parallel_group}' "
                f"but failure_mode='fail'. Parallel nodes must be degradable."
            )


# ── Data-flow topology sanity ─────────────────────────────────────────────


def test_no_node_reads_and_writes_same_field():
    skip = {
        "agent_statuses",
        "retry_questions",
        "research_iteration",
        "policy_decision",
    }
    for agent_id, entry in AGENT_REGISTRY.items():
        overlap = (entry.reads & entry.writes) - skip
        assert not overlap, f"{agent_id} reads and writes same fields: {overlap}"


def test_research_writes_feed_all_analysis_nodes():
    research_writes = AGENT_REGISTRY["research"].writes
    for node in ("fundamental_analysis", "macro_analysis", "market_sentiment"):
        assert "evidence" in research_writes
        assert "evidence" in AGENT_REGISTRY[node].reads, f"{node} doesn't read evidence"


def test_scoring_output_feeds_debate():
    assert "scenarios" in AGENT_REGISTRY["scenario_scoring"].writes
    assert "scenarios" in AGENT_REGISTRY["scenario_debate"].reads


def test_debate_output_feeds_report():
    assert "scenario_debate" in AGENT_REGISTRY["scenario_debate"].writes
    assert "scenario_debate" in AGENT_REGISTRY["report_finalize"].reads


def test_plan_context_flows_correctly():
    assert "plan_context" in AGENT_REGISTRY["parse_intent"].writes
    for node in (
        "research",
        "fundamental_analysis",
        "scenario_scoring",
        "report_finalize",
    ):
        assert "plan_context" in AGENT_REGISTRY[node].reads, (
            f"{node} doesn't read plan_context"
        )


# ── depends_on graph is acyclic (basic check) ────────────────────────────


def test_depends_on_no_self_reference():
    for agent_id, entry in AGENT_REGISTRY.items():
        assert agent_id not in entry.depends_on, f"{agent_id} depends on itself"


def test_depends_on_references_known_agents():
    known = set(AGENT_REGISTRY.keys())
    for agent_id, entry in AGENT_REGISTRY.items():
        for dep in entry.depends_on:
            assert dep in known, f"{agent_id} depends_on unknown agent '{dep}'"
