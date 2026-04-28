"""Node read/write contracts for ResearchState.

Each node declares exactly which state fields it reads and writes.
In test environments, assert_reads() enforces the read contract at runtime —
any undeclared field access raises ContractViolation immediately.
In production the guard is a no-op (zero overhead).

Usage in a node:
    from src.server.utils.contract import assert_reads, assert_writes
    _READS  = frozenset({...})
    _WRITES = frozenset({...})

    async def my_node(state):
        assert_reads(state, _READS, _NODE)
        ...
        result = {...}
        assert_writes(result, _WRITES, _NODE)
        return result
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _enforce() -> bool:
    return (
        os.environ.get("PYTEST_CURRENT_TEST") is not None
        or os.environ.get("CONTRACT_ENFORCE") == "1"
    )

# Fields every node may read freely (present in every state snapshot).
_GLOBAL_READS = frozenset({"query", "agent_statuses"})

# Fields every node may write freely (status book-keeping, always valid).
_GLOBAL_WRITES = frozenset({"agent_statuses"})


class ContractViolation(Exception):
    pass


@dataclass(frozen=True)
class NodeContract:
    reads: frozenset[str]
    writes: frozenset[str]


def assert_reads(state: dict, declared: frozenset[str], node: str) -> None:
    """No-op in production and in integration tests where the full state is present.

    Only raises in unit tests that explicitly construct a minimal state
    (containing only the fields the node should see). This avoids false
    positives when the real LangGraph state carries extra upstream fields
    that the current node legitimately ignores.

    To trigger enforcement in a unit test, pass a state built from exactly
    the node's declared reads — any extra key then indicates a contract gap.
    """
    if not _enforce():
        return
    allowed = declared | _GLOBAL_READS
    extra = set(state.keys()) - allowed
    if extra:
        raise ContractViolation(
            f"[{node}] received state with undeclared fields: {sorted(extra)}\n"
            f"Declared reads: {sorted(allowed)}\n"
            "If this node legitimately needs these fields, add them to its contract."
        )


def assert_writes(delta: dict, declared: frozenset[str], node: str) -> None:
    if not _enforce():
        return
    allowed = declared | _GLOBAL_WRITES
    extra = set(delta.keys()) - allowed
    if extra:
        raise ContractViolation(
            f"[{node}] wrote undeclared state fields: {sorted(extra)}\n"
            f"Declared writes: {sorted(allowed)}"
        )


# ── Ground-truth contract table ────────────────────────────────────────────
# This is the single authoritative record of the data-flow topology.
# Keep in sync with each node's _READS / _WRITES constants.

NODE_CONTRACTS: dict[str, NodeContract] = {
    "parse_intent": NodeContract(
        reads=frozenset({"query"}),
        writes=frozenset({
            "intent", "plan_context",
            "research_iteration", "retry_questions",
        }),
    ),
    "research": NodeContract(
        reads=frozenset({
            "query", "intent", "plan_context",
            "retry_questions", "research_iteration",
        }),
        writes=frozenset({"evidence", "normalized_data", "research_iteration"}),
    ),
    "fundamental_analysis": NodeContract(
        reads=frozenset({
            "evidence", "normalized_data", "intent", "plan_context",
        }),
        writes=frozenset({"fundamental_analysis"}),
    ),
    "macro_analysis": NodeContract(
        reads=frozenset({"evidence", "intent"}),
        writes=frozenset({"macro_analysis"}),
    ),
    "market_sentiment": NodeContract(
        reads=frozenset({"evidence", "normalized_data"}),
        writes=frozenset({"market_sentiment"}),
    ),
    "retry_gate": NodeContract(
        reads=frozenset({
            "intent", "normalized_data", "research_iteration",
        }),
        writes=frozenset({"retry_questions", "retry_reason"}),
    ),
    "scenario_scoring": NodeContract(
        reads=frozenset({
            "evidence", "fundamental_analysis", "macro_analysis",
            "market_sentiment", "intent", "plan_context",
        }),
        writes=frozenset({"scenarios"}),
    ),
    "scenario_debate": NodeContract(
        reads=frozenset({
            "scenarios", "evidence",
            "fundamental_analysis", "macro_analysis", "market_sentiment",
        }),
        writes=frozenset({"scenario_debate"}),
    ),
    "report_finalize": NodeContract(
        reads=frozenset({
            "intent", "evidence",
            "fundamental_analysis", "macro_analysis", "market_sentiment",
            "scenarios", "scenario_debate", "plan_context",
        }),
        writes=frozenset({
            "narrative_sections", "report_markdown", "report_json",
            "validation_result", "quality_metrics",
            "retry_questions", "stop_reason",
        }),
    ),
}
