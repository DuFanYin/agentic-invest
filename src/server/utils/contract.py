"""Node read/write contracts for ResearchState.

NODE_CONTRACTS is derived from AGENT_REGISTRY (agents/registry.py), which is
the single source of truth for reads/writes. Do not hand-maintain contracts here.

In test environments, assert_reads() enforces the read contract at runtime —
any undeclared field access raises ContractViolation immediately.
In production the guard is a no-op (zero overhead).

Usage in a node:
    from src.server.utils.contract import assert_reads, assert_writes
    _READS  = NODE_CONTRACTS["my_node"].reads
    _WRITES = NODE_CONTRACTS["my_node"].writes

    async def my_node(state):
        assert_reads(state, _READS, "my_node")
        ...
        result = {...}
        assert_writes(result, _WRITES, "my_node")
        return result
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _enforce() -> bool:
    if os.environ.get("CONTRACT_ENFORCE") == "1":
        return True
    current_test = os.environ.get("PYTEST_CURRENT_TEST")
    if not current_test:
        return False
    # Integration tests execute full LangGraph state snapshots that include
    # upstream fields many nodes do not explicitly read.
    if "tests/integration/" in current_test:
        return False
    return True


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


# ── Derived from AGENT_REGISTRY — do not edit manually ────────────────────

def _build_contracts() -> dict[str, NodeContract]:
    # Import here to avoid circular import (registry imports nothing from utils/)
    from src.server.agents.registry import AGENT_REGISTRY
    return {
        agent_id: NodeContract(reads=entry.reads, writes=entry.writes)
        for agent_id, entry in AGENT_REGISTRY.items()
    }


NODE_CONTRACTS: dict[str, NodeContract] = _build_contracts()
