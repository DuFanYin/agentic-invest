# Codebase Structure

## 1) Goal

This repository is organized around one backend research pipeline, one lightweight frontend, shared typed models, and supporting tests/docs. The backend follows a layered structure:

- `routes -> agents -> services`
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
│   │   │   ├── request.py          # Request payload models
│   │   │   ├── response.py         # Response, status, and telemetry models
│   │   │   ├── analysis.py         # Analysis and planning payload models
│   │   │   ├── finance.py          # Finance data payload models
│   │   │   ├── state.py            # LangGraph shared state definition
│   │   │   ├── evidence.py         # Evidence item schema
│   │   │   ├── intent.py           # Research intent schema
│   │   │   └── scenario.py         # Scenario schema
│   │   ├── agents/              # Graph runtime and node implementations
│   │   ├── services/            # External clients and runtime infra
│   │   │   ├── __init__.py        
│   │   │   ├── openrouter.py       # LLM client wrapper
│   │   │   ├── finance_data.py     # yfinance data access
│   │   │   ├── macro_data.py       # FRED and macro signal access
│   │   │   ├── web_research.py     # Tavily web search client
│   │   │   ├── cache.py            # SQLite TTL cache
│   │   │   ├── collector.py        # Per-request LLM call collector
│   │   │   └── section_queue.py    # Section streaming queue
│   │   └── utils/               # Stateless runtime helpers
│   │       ├── __init__.py      
│   │       ├── contract.py         # Node read/write contracts
│   │       ├── status.py           # Agent status helpers
│   │       └── validation.py       # Final validation helpers
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
- `tests/`: automated coverage split into unit tests and one integration API flow test
- `outputs/`: example generated report artifact

## 4) Server Overview

`src/server/` contains the FastAPI app and the full research runtime. Its main structure is:

- `routes/`: HTTP and SSE entrypoints
- `agents/`: LangGraph orchestration and node implementations
- `services/`: third-party integrations and process-local infrastructure
- `models/`: typed contracts shared across layers
- `utils/`: stateless runtime helpers

The request path is:

`POST /research` or `POST /research/stream` -> `OrchestratorAgent` -> LangGraph nodes -> typed response or SSE events

## 5) Server Entry and App Lifecycle

### `src/server/config.py`

Loads `.env` once at import time via an upward directory walk (up to 8 levels) and exposes typed module-level constants. All services import configuration here rather than calling `os.getenv` ad hoc.

| Constant | Purpose |
|---|---|
| `LLM_PROVIDER` | LLM provider (`openrouter` or `openai`) |
| `LLM_API_KEY` | LLM auth |
| `TAVILY_API_KEY` | web search auth |
| `FRED_API_KEY` | FRED macro indicator auth |

### `src/server/main.py`

Creates the FastAPI application, mounts static files, and registers the route blueprints. Exposes `/`, `/health`, `/research`, and `/research/stream`. It also manages app lifespan by initializing and clearing the process-wide shutdown signal used by streaming and retry backoff paths.

### `src/server/shutdown.py`

Process-wide shutdown signaling shared across async and sync contexts.

- `init_async_event()`: enable shutdown signaling for active server lifecycle
- `set()` / `clear()` / `is_set()`: shared signal helpers for graceful interruption
- `wait_or_timeout(timeout)`: interruptible sleep primitive used by retry backoff
- `disable()`: deactivate signaling outside active app lifecycle

## 6) HTTP Layer

### `src/server/routes/`

Thin transport layer only; no core research logic.

- `health.py`: `GET /health` liveness check
- `research.py`: `POST /research` and `POST /research/stream`

`research.py` creates a fresh `OrchestratorAgent` per request. The streaming route emits an initial `agent_status` snapshot immediately, then forwards domain events from `run_stream()` as SSE `event` frames. On failure it maps the error back to the most relevant node when possible and emits both `agent_status` and `error` before `done`.

## 7) Shared Models and State

### `src/server/models/`

Pydantic models and typed state contracts shared across the stack.

| File | Contents |
|---|---|
| `request.py` | `ResearchRequest` |
| `response.py` | `ResearchResponse`, `AgentStatus`, `ValidationResult`, `LLMCall` |
| `analysis.py` | typed analysis and planning payloads (`FundamentalAnalysis`, `MacroAnalysis`, `MarketSentiment`, `ScenarioDebate`, `NormalizedData`, `PlanContext`, `ReportPlan`, `CustomSection`, `QualityMetrics`, `Budget`) |
| `finance.py` | finance service payload contracts |
| `state.py` | `ResearchState` (`TypedDict`) with reducers |
| `evidence.py` | `Evidence` (`url` is `Optional[str]`) |
| `intent.py` | `ResearchIntent` |
| `scenario.py` | `Scenario` (`probability: float` in `[0, 1]`) |

`ResearchState` is the LangGraph handoff object between nodes. Key reducer rules:

- `evidence`: `operator.add` so retry passes accumulate evidence
- `retry_questions`: plain replace so only the current retry request remains active
- `agent_statuses`: `_last_list` custom reducer so concurrent node writes merge instead of colliding

## 8) Agent Runtime

### `src/server/agents/`

Refer to [`design/core-agent-system.md`](design/core-agent-system.md) for detailed agent documentation.

## 9) Services

### `src/server/services/`

External dependency wrappers and request-scoped infrastructure. Agents do not call third-party APIs directly.

Capability groups:

- LLM access: `openrouter.py`
- data collection: `finance_data.py`, `macro_data.py`, `web_research.py`
- process-local infrastructure: `cache.py`, `collector.py`, `section_queue.py`

#### `openrouter.py`

OpenAI-compatible LLM client used by the agent nodes. With the default OpenRouter provider it rotates through a four-model free-tier chain:

`openai/gpt-oss-120b:free → qwen/qwen3-next-80b-a3b-instruct:free → meta-llama/llama-3.3-70b-instruct:free → google/gemma-3-27b-it:free`

Per model, it retries on 429/5xx with exponential backoff. Internal error classes split retryable failures from immediate failover:

- `_RetryableError`: retry the same model
- `_FatalError`: skip to the next model

Public interface:

| Method | Mode | Notes |
|---|---|---|
| `complete(prompt)` | JSON | validates JSON before returning |
| `complete_json(prompt)` | JSON | parses and returns `dict` |
| `complete_text(prompt)` | text | free-form Markdown; no JSON enforcement |
| `call_with_retry(prompt, attempts=2)` | JSON | agent-level retry wrapper used by analysis nodes |

The client also emits telemetry to `LLMCallCollector` when one is attached.

#### `finance_data.py`

yfinance wrapper providing `get_info()`, `get_financials()`, `get_price_history()`, and `get_news()`. Helper functions convert numpy/DataFrame values into safe Python primitives.

#### `macro_data.py`

Macro data wrapper around FRED and yfinance macro tickers.

- `get_fred_indicators()`: latest FRED series with direction tags, cached 6h
- `get_market_signals()`: VIX/10Y/USD market signals, cached 15m
- `get_all()`: concurrent fetch of both data groups

#### `web_research.py`

Tavily search wrapper via `httpx`. If `TAVILY_API_KEY` is absent, it degrades gracefully and returns `[]`.

#### `cache.py`

SQLite-backed TTL cache with `threading.Lock` and WAL mode.

- `get(key) -> object | None`
- `set(key, value, ttl_seconds)`
- `delete(key)`
- `clear_expired()`

TTL is enforced on read, so expired entries behave as cache misses.

#### `collector.py`

Per-request LLM telemetry collector used by `OrchestratorAgent` and `OpenRouterClient`.

- `record(call)`: append and enqueue `LLMCall` events
- `wait_next()`: async dequeue for streaming `llm_call` events
- `all()`: snapshot for final `ResearchResponse.llm_calls`

#### `section_queue.py`

Per-request queue for section streaming during report finalization.

- `push(section_id, content, source, title="")`: enqueue `section_ready`
- `done()`: enqueue terminal sentinel
- used by `report_finalize` as producer and `orchestrator.run_stream` as consumer

## 10) Utilities

### `src/server/utils/`

Stateless runtime helpers.

- `contract.py`: node read/write contracts (`NODE_CONTRACTS`) and enforcement helpers (`assert_reads`, `assert_writes`)
- `status.py`: `initial_agent_statuses()` and `update_status()` for `AgentStatus` snapshots without in-place mutation
- `validation.py`: final validation helpers for scenario scores, evidence completeness, and claim coverage

## 11) Frontend

### `src/frontend/`

React-in-HTML frontend without a build step; React, ReactDOM, and Babel are loaded from CDNs.

Refer to [`design/frontend.md`](design/frontend.md) for detailed frontend documentation.

## 12) Tests and Outputs

### `tests/`

Refer to [`design/test-suite.md`](design/test-suite.md) for detailed test documentation.

### `outputs/`

- `sample-report.md`: example generated report output