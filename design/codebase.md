# Codebase Onboarding Map

This document is a navigation map for the repository. It explains the main layers, how a request moves through the system, and where to make common changes.

For the runtime graph itself, read [`core-system.md`](core-system.md). For LLM calls and expected formats, read [`llm-callpoints.md`](llm-callpoints.md).

## 1) Mental Model

The backend is a FastAPI app wrapped around a LangGraph research workflow.

The main layering is:

```text
routes -> agents -> capabilities -> services
                   ↘ models / utils
```

- `routes` expose HTTP and SSE endpoints.
- `agents` own graph nodes and state transitions.
- `capabilities` are reusable research units called by `research`.
- `services` wrap external systems and runtime infrastructure.
- `models` define typed payloads and shared graph state.
- `utils` provide contract, status, and validation helpers.

## 2) Top-Level Repository Map

```text
agentic-invest/
├── src/
│   ├── server/        # backend runtime
│   └── frontend/      # static frontend
├── tests/             # unit and integration tests
├── design/            # architecture docs
└── outputs/           # generated artifacts and local cache
```

## 3) Backend Layers

### `src/server/routes/`

Transport layer only.

- `health.py`: health endpoint.
- `research.py`: `/research` and `/research/stream`.

### `src/server/agents/`

LangGraph node implementations and orchestration.

Important files:

- `orchestrator.py`: builds and runs the graph.
- `research.py`: coordinates capability calls and normalization.
- `llm_judge.py`: turns evidence/analysis gaps into a policy hint.
- `policy_router.py`: finalizes retry/continue routing.
- `report_finalize.py`: assembles report outputs.
- `registry.py`: source of truth for node reads/writes, dependencies, and failure mode.

### `src/server/capabilities/`

Research capability layer called by `research`.

- `finance.py`: finance evidence and metrics.
- `macro.py`: macro evidence.
- `web.py`: web evidence, including multi-query fetch.
- `normalize.py`: `NormalizedData`, missing fields, and conflict signals.

### `src/server/services/`

External clients and runtime support.

- `llm_provider.py`: model calls, retries, failover, and telemetry.
- `finance_data.py`, `macro_data.py`, `web_research.py`: data sources.
- `policy.py`: deterministic policy rules.
- `cache.py`: SQLite TTL cache.
- `collector.py`: per-request LLM call stream.
- `section_queue.py`: per-section report streaming.
- `retry.py`: shared fetch retry helpers.

### `src/server/models/`

Typed contracts shared across the graph.

- `state.py`: LangGraph `ResearchState` and reducers.
- `analysis.py`: analysis outputs, planning context, report plans, quality metrics.
- `evidence.py`, `intent.py`, `scenario.py`, `response.py`, `request.py`: shared API/domain models.

### `src/server/utils/`

Small stateless helpers.

- `contract.py`: derives `NODE_CONTRACTS` from `agents/registry.py`.
- `status.py`: agent status snapshots and updates.
- `validation.py`: report/scenario/citation validation.

## 4) Request Lifecycle

One-shot request:

```text
POST /research
  -> OrchestratorAgent.run()
  -> LangGraph workflow
  -> ResearchResponse
```

Streaming request:

```text
POST /research/stream
  -> OrchestratorAgent.run_stream()
  -> agent_status + llm_call + section_ready + final/error/done events
```

Graph details live in [`core-system.md`](core-system.md).