"""Agent registry — single source of truth for per-agent metadata.

Each AgentRegistryEntry records:
- data-flow contracts (reads/writes) — derived into NODE_CONTRACTS in contract.py
- scheduling intent (parallel_group, depends_on)
- failure semantics (failure_mode)
- capability dependencies (capability_deps) — used by Goal 2 selective rerun

Adding a new agent: add one entry here. No other file needs to change for
data-flow or scheduling intent (orchestrator.py still wires the edges).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class AgentRegistryEntry:
    agent_id: str

    # Data-flow contracts — authority for contract.py NODE_CONTRACTS
    reads: frozenset[str]
    writes: frozenset[str]

    # Scheduling intent
    parallel_group: str | None   # nodes in the same group run concurrently in the graph
    depends_on: list[str]        # upstream agents whose writes this node needs

    # Failure semantics
    failure_mode: Literal["fail", "degrade"]
    # "fail"    → raises RuntimeError; LangGraph propagates → pipeline stops
    # "degrade" → returns minimal valid object with degraded=True; pipeline continues

    # Capability dependencies (for Goal 2 retry_capability_only / rerun_agent_only)
    capability_deps: list[str] = field(default_factory=list)

    # Reserved for Goal 2 policy layer
    retry_policy: str | None = None


AGENT_REGISTRY: dict[str, AgentRegistryEntry] = {
    "parse_intent": AgentRegistryEntry(
        agent_id="parse_intent",
        reads=frozenset({"query"}),
        writes=frozenset({
            "intent", "plan_context",
            "research_iteration", "retry_questions",
        }),
        parallel_group=None,
        depends_on=[],
        failure_mode="fail",
        capability_deps=[],
    ),
    "research": AgentRegistryEntry(
        agent_id="research",
        reads=frozenset({
            "query", "intent", "plan_context",
            "retry_questions", "retry_scope", "research_iteration",
        }),
        writes=frozenset({"evidence", "normalized_data", "research_iteration"}),
        parallel_group=None,
        depends_on=["parse_intent"],
        failure_mode="fail",
        capability_deps=["cap.fetch_finance", "cap.fetch_macro", "cap.fetch_web", "cap.normalize"],
    ),
    "fundamental_analysis": AgentRegistryEntry(
        agent_id="fundamental_analysis",
        reads=frozenset({
            "evidence", "normalized_data", "intent", "plan_context",
        }),
        writes=frozenset({"fundamental_analysis"}),
        parallel_group="analysis",
        depends_on=["research"],
        failure_mode="degrade",
        capability_deps=["cap.fetch_finance"],
    ),
    "macro_analysis": AgentRegistryEntry(
        agent_id="macro_analysis",
        reads=frozenset({"evidence", "intent"}),
        writes=frozenset({"macro_analysis"}),
        parallel_group="analysis",
        depends_on=["research"],
        failure_mode="degrade",
        capability_deps=["cap.fetch_macro"],
    ),
    "market_sentiment": AgentRegistryEntry(
        agent_id="market_sentiment",
        reads=frozenset({"evidence", "normalized_data", "intent"}),
        writes=frozenset({"market_sentiment"}),
        parallel_group="analysis",
        depends_on=["research"],
        failure_mode="degrade",
        capability_deps=["cap.fetch_web", "cap.fetch_finance"],
    ),
    "llm_judge": AgentRegistryEntry(
        agent_id="llm_judge",
        reads=frozenset({
            "intent", "normalized_data", "evidence", "plan_context",
            "fundamental_analysis", "macro_analysis", "market_sentiment",
            "research_iteration", "retry_questions",
        }),
        writes=frozenset({"policy_decision"}),
        parallel_group=None,
        depends_on=["fundamental_analysis", "macro_analysis", "market_sentiment"],
        failure_mode="degrade",
        capability_deps=[],
    ),
    "policy_router": AgentRegistryEntry(
        agent_id="policy_router",
        reads=frozenset({
            "policy_decision", "evidence", "normalized_data",
            "fundamental_analysis", "macro_analysis", "market_sentiment",
            "research_iteration",
        }),
        writes=frozenset({"policy_decision", "retry_questions", "retry_reason", "retry_scope"}),
        parallel_group=None,
        depends_on=["llm_judge"],
        failure_mode="fail",   # routing broken = unrecoverable
        capability_deps=[],
    ),
    "scenario_scoring": AgentRegistryEntry(
        agent_id="scenario_scoring",
        reads=frozenset({
            "evidence", "fundamental_analysis", "macro_analysis",
            "market_sentiment", "intent", "plan_context",
        }),
        writes=frozenset({"scenarios"}),
        parallel_group=None,
        depends_on=["llm_judge"],
        failure_mode="fail",
        capability_deps=[],
    ),
    "scenario_debate": AgentRegistryEntry(
        agent_id="scenario_debate",
        reads=frozenset({
            "scenarios", "evidence",
            "fundamental_analysis", "macro_analysis", "market_sentiment",
        }),
        writes=frozenset({"scenario_debate"}),
        parallel_group=None,
        depends_on=["scenario_scoring"],
        failure_mode="degrade",
        capability_deps=[],
    ),
    "report_finalize": AgentRegistryEntry(
        agent_id="report_finalize",
        reads=frozenset({
            "intent", "evidence",
            "fundamental_analysis", "macro_analysis", "market_sentiment",
            "scenarios", "scenario_debate", "plan_context",
            "research_iteration", "retry_reason",
        }),
        writes=frozenset({
            "narrative_sections", "report_markdown", "report_json",
            "validation_result", "quality_metrics",
            "retry_questions", "stop_reason",
        }),
        parallel_group=None,
        depends_on=["scenario_debate"],
        failure_mode="fail",
        capability_deps=[],
    ),
}
