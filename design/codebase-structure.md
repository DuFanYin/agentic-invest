# Codebase Structure

## 1) Goal

This repository is organized around one backend research pipeline, one lightweight frontend, shared typed models, and supporting tests/docs. The backend follows a layered structure:

- `routes -> agents -> capabilities -> services`
- `models` and `utils` sit underneath as shared contracts and helpers

## 2) Folder Structure

```text
agentic-invest/
├── design/
├── src/
│   ├── server/
│   │   ├── config.py
│   │   ├── main.py
│   │   ├── shutdown.py
│   │   ├── routes/              # HTTP and SSE endpoints
│   │   │   ├── __init__.py
│   │   │   ├── health.py           # Health check endpoint
│   │   │   └── research.py         # Research and streaming endpoints
│   │   ├── models/              # Shared typed contracts
│   │   │   ├── __init__.py
│   │   │   ├── request.py
│   │   │   ├── response.py
│   │   │   ├── analysis.py
│   │   │   ├── finance.py
│   │   │   ├── state.py
│   │   │   ├── evidence.py
│   │   │   ├── intent.py
│   │   │   └── scenario.py
│   │   ├── agents/              # Graph runtime and node implementations
│   │   ├── capabilities/        # research capability layer (finance/macro/web/normalize)
│   │   ├── services/            # External clients and runtime infra
│   │   │   ├── __init__.py
│   │   │   ├── llm_provider.py
│   │   │   ├── finance_data.py
│   │   │   ├── macro_data.py
│   │   │   ├── web_research.py
│   │   │   ├── cache.py
│   │   │   ├── collector.py
│   │   │   ├── retry.py
│   │   │   ├── section_queue.py
│   │   │   └── policy.py        # PolicyInput/PolicyDecision + deterministic rule engine
│   │   └── utils/
│   │       ├── __init__.py
│   │       ├── contract.py
│   │       ├── status.py
│   │       └── validation.py
│   └── frontend/
│       ├── index.html
│       └── static/
│           └── styles.css
├── tests/
│   ├── unit/
│   └── integration/
│       └── test_research_api.py
└── outputs/
    └── sample-report.md
```

## 3) Repository Areas

- `design/`: design documentation only; not imported at runtime
- `src/`: runtime code split into backend (`src/server/`) and frontend (`src/frontend/`)
- `tests/`: automated coverage split into unit tests and integration tests
- `outputs/`: generated artifacts (sample report, cache db, etc.)

## 4) Server Overview

`src/server/` contains the FastAPI app and the full research runtime. Its main structure is:

- `routes/`: HTTP and SSE entrypoints
- `agents/`: LangGraph orchestration and node implementations
- `capabilities/`: typed research capability units used by `research`
- `services/`: third-party integrations and process-local infrastructure
- `models/`: typed contracts shared across layers
- `utils/`: stateless runtime helpers

The request path is:

`POST /research` or `POST /research/stream` -> `OrchestratorAgent` -> LangGraph nodes -> typed response or SSE events

## 5) Server Entry and App Lifecycle

### `src/server/config.py`

Loads `.env` from a fixed repository-root path at import time and exposes typed module-level constants. Startup fails fast if `python-dotenv` is missing or `.env` is not present.

| Constant | Purpose |
|---|---|
| `LLM_PROVIDER` | LLM provider (`openrouter` or `openai`) |
| `LLM_API_KEY` | LLM auth |
| `TAVILY_API_KEY` | web search auth |
| `FRED_API_KEY` | FRED macro indicator auth |
| `CACHE_DB_PATH` | SQLite cache location |
| `REQUEST_TIMEOUT_SECONDS` | request-level orchestration timeout (180s) |

### `src/server/main.py`

Creates the FastAPI app, mounts static files, registers routes, and manages app lifespan with shutdown signaling.

### `src/server/shutdown.py`

Process-wide shutdown signaling for retry backoff and streaming paths.

## 6) HTTP Layer

### `src/server/routes/`

Thin transport layer only; no core research logic.

- `health.py`: `GET /health`
- `research.py`: `POST /research` and `POST /research/stream`

Streaming emits initial statuses immediately, then forwards graph events (`agent_status`, `llm_call`, `section_ready`, `final`, `error`, `done`).

## 7) Shared Models and State

### `src/server/models/`

Pydantic models and typed state contracts shared across the stack.

`ResearchState` (`state.py`) is the LangGraph handoff object between nodes. Key reducers:

- `evidence`: `operator.add` (accumulate across iterations)
- `retry_questions`: plain replace
- `agent_statuses`: `_last_list` (merge parallel writes by agent)

State also includes policy routing fields:

- `policy_decision`
- `retry_scope`

## 8) Agent Runtime

### `src/server/agents/`

Refer to [`design/core-agent-system.md`](design/core-agent-system.md) for detailed agent behavior.

Current retry routing path is:

- `llm_judge -> policy_router -> (research | scenario_scoring)`

## 9) Capabilities

### `src/server/capabilities/`

Used by `research` to keep orchestration thin:

- `finance.py`: fetch + assemble finance evidence/metrics
- `macro.py`: fetch + assemble macro evidence
- `web.py`: fetch web evidence (supports multi-query concurrent fetch)
- `normalize.py`: build `NormalizedData` and conflicts

## 10) Services

### `src/server/services/`

External dependency wrappers and request-scoped infrastructure.

- LLM access: `llm_provider.py`
- data collection: `finance_data.py`, `macro_data.py`, `web_research.py`
- policy evaluation: `policy.py`
- process-local infra: `cache.py`, `collector.py`, `section_queue.py`, `retry.py`

`llm_provider.py` exposes:

- `complete(...)` (JSON mode)
- `complete_text(...)` (text mode)
- `call_with_retry(...)` (JSON mode + simplified-prompt retry on invalid JSON)

## 11) Utilities

### `src/server/utils/`

Stateless runtime helpers.

- `contract.py`: `NODE_CONTRACTS` derived from `agents/registry.py`; `assert_reads` / `assert_writes`
- `status.py`: status snapshot helpers
- `validation.py`: report/scenario/claim validation helpers

## 12) Frontend

### `src/frontend/`

React-in-HTML frontend without a build step.

Refer to [`design/frontend.md`](design/frontend.md) for UI details.

## 13) Tests and Outputs

### `tests/`

Unit and integration coverage for agents, services, policy routing, and API.

### `outputs/`

Runtime artifacts (e.g., sample report, SQLite cache).