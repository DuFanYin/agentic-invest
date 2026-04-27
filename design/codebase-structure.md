# Codebase Structure

## 1) Goal

The repository is organised by backend services, frontend pages, core agent system, shared models, tests, and docs.
The architecture is layered: routes -> agents -> services, with shared models/utils beneath them.

## 2) Folder Structure

```text
agentic-invest/
├── design/
├── src/
│   ├── server/
│   │   ├── config.py
│   │   ├── main.py
│   │   ├── shutdown.py
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── health.py
│   │   │   └── research.py
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── request.py
│   │   │   ├── response.py
│   │   │   ├── analysis.py
│   │   │   ├── finance.py
│   │   │   ├── state.py
│   │   │   ├── evidence.py
│   │   │   ├── intent.py
│   │   │   └── scenario.py
│   │   ├── agents/
│   │   │   ├── __init__.py
│   │   │   ├── orchestrator.py
│   │   │   ├── planning_agent.py
│   │   │   ├── research.py
│   │   │   ├── fundamental_analysis.py
│   │   │   ├── macro_analysis.py
│   │   │   ├── market_sentiment.py
│   │   │   ├── retry_gate.py
│   │   │   ├── scenario_scoring.py
│   │   │   ├── scenario_debate.py
│   │   │   └── report_finalize.py
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── openrouter.py
│   │   │   ├── finance_data.py
│   │   │   ├── macro_data.py
│   │   │   ├── web_research.py
│   │   │   ├── cache.py
│   │   │   └── collector.py
│   │   └── utils/
│   │       ├── __init__.py
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

## 3) Directory Responsibilities

### `design/`

Design documentation only — not imported at runtime.

- `core-agent-system.md`: multi-agent architecture, graph topology, data contracts
- `test-suite.md`: per-file test coverage summary and run commands
- `frontend.md`: React-in-HTML UI layout, SSE wiring, and section reveal behavior
- `peripheral-plan.md`: peripheral architecture and integration planning
- `llm-callpoints-and-expected-formats.md`: all LLM callsites and expected payload formats
- `codebase-structure.md`: this file

### `src/server/config.py`

Loads `.env` once at import time via an upward directory walk (up to 8 levels).
Exposes typed module-level constants:

| Constant | Purpose |
|---|---|
| `LLM_PROVIDER` | LLM provider (`openrouter` or `openai`) |
| `LLM_API_KEY` | LLM auth |
| `LLM_BASE_URL` | provider API base URL (auto-derived if omitted) |
| `LLM_HTTP_REFERER` | optional OpenRouter ranking header |
| `LLM_APP_TITLE` | optional OpenRouter ranking header |
| `TAVILY_API_KEY` | web search auth |
| `FRED_API_KEY` | FRED macro indicator auth |

All services import from here — no scattered `os.getenv` calls elsewhere.

### `src/server/main.py`

Creates the FastAPI application, mounts static files, and registers route blueprints.
Exposes: `/` (frontend), `/health`, `/research`, `/research/stream`.
Manages app lifespan by initializing and clearing the process-wide shutdown signal used by streaming and retry backoff paths.

### `src/server/shutdown.py`

Process-wide shutdown signaling used across async/sync contexts.

- `init_async_event()`: enables shutdown signaling for active server lifecycle
- `set()` / `clear()` / `is_set()`: shared signal helpers for graceful stream/retry interruption
- `wait_or_timeout(timeout)`: interruptible sleep primitive used by retry backoff
- `disable()`: deactivates signaling outside active app lifecycle

### `src/server/routes/`

HTTP layer only — no business logic.

- `health.py`: `GET /health` liveness check
- `research.py`: `POST /research` (async) and `POST /research/stream` (SSE).
  Creates a fresh `OrchestratorAgent` per request.

### `src/server/models/`

Pydantic models shared across all layers.

| File | Contents |
|---|---|
| `request.py` | `ResearchRequest` |
| `response.py` | `ResearchResponse`, `AgentStatus`, `ValidationResult` |
| `analysis.py` | typed analysis payloads (`FundamentalAnalysis`, `MacroAnalysis`, `MarketSentiment`, `ScenarioDebate`, `NormalizedData`) |
| `finance.py` | typed finance service payload contracts |
| `state.py` | `ResearchState` (LangGraph `TypedDict`) with annotated reducers |
| `evidence.py` | `Evidence` — `url` is `Optional[str]` |
| `intent.py` | `ResearchIntent` |
| `scenario.py` | `Scenario` — `probability: float` in `[0, 1]` |

`ResearchState` reducer notes:
- `evidence`: `operator.add` — parallel passes accumulate items
- `agent_questions`: `_accumulate_or_reset` — parallel analysis nodes append missing-field questions; `retry_gate` clears via sentinel
- `retry_questions`: plain replace — `retry_gate` and `report_finalize` update this cycle-by-cycle
- `agent_statuses`: `_last_list` custom reducer — merges concurrent writes by agent and prefers newer `last_update_at` snapshots

### `src/server/agents/`

LangGraph node functions and the graph wiring.

#### `orchestrator.py`

Builds and owns the `StateGraph`. Graph topology:

```
START → parse_intent(planning_agent) → research → [parallel] → retry_gate ─(gaps?)─→ research (retry, ≤2 iterations)
                                        fundamental_analysis               └─(no gaps)─→ scenario_scoring
                                        macro_analysis                                   → scenario_debate
                                        market_sentiment                                 → report_finalize
                                                                                    └─(unsupported claims + retry budget)→ research
                                                                                    └─(otherwise)→ END
```

- `make_planning_node(llm_client)`: delegates parsing/planning to `planning_agent`
- `retry_gate_node`: merges structural gaps (ticker/horizon), agent-raised questions, and research conflict signals; clears `retry_questions` after `MAX_RESEARCH_ITERATIONS = 2`
- `retry_router`: returns `"research"` or `"scenario_scoring"`
- `build_graph(llm_client)`: compiles the graph per request execution
- `OrchestratorAgent.run()`: async invoke (`graph.ainvoke`)
- `OrchestratorAgent.run_stream()`: yields `agent_status`, `llm_call`, and `final` domain events (route layer wraps with SSE `event` names, plus `error`/`done` control events)

#### `research.py`

Collects evidence from finance, macro, and web sources with in-process SQLite caching:

| Source | TTL | Data |
|---|---|---|
| `FinanceDataClient` | 3600 s | company info, financials, price history, yfinance news |
| `MacroDataClient` | 21600 s (FRED), 900 s (market signals) | FRED indicators + macro market signals (VIX/rates/USD) |
| `WebResearchClient` | 900 s | Tavily web search results |

Cache keys are `<prefix>:sha256(":".join(parts))[:16]`. Deduplicates web URLs. If no usable evidence can be collected, the node raises a runtime error (no synthetic evidence fallback).

Returns: `evidence` (list appended via reducer), `normalized_data` (metrics, missing_fields, conflict signals), incremented `research_iteration`.

#### `fundamental_analysis.py`

LLM node (runs in parallel with `market_sentiment`).
Uses `_llm.call_with_retry()` → `json.loads()`.
Returns: business quality view, valuation view, claims with evidence citations, fundamental risks, missing fields.
If LLM output is unavailable/invalid, the node raises a runtime error (no stub fallback).

#### `market_sentiment.py`

LLM node (runs in parallel with `fundamental_analysis`).
Filters evidence to `news`/`web` source types for the prompt.
Returns: news sentiment direction, price action view, market narrative, sentiment risks.
If LLM output is unavailable/invalid, the node raises a runtime error (no stub fallback).

#### `scenario_scoring.py`

LLM node (sequential, after `retry_gate` passes).
LLM returns raw weights (`raw_probability`); Python normalises to `sum(probability) = 1` before constructing `Scenario` objects.
Requires 3-5 scenarios from the model; out-of-range counts are treated as invalid output.
If scenario generation fails, the node raises a runtime error (no stub scenario fallback).

#### `planning_agent.py`

Converts raw query into `ResearchIntent` plus planning fields (`research_focus`, `must_have_metrics`, `plan_notes`).

#### `macro_analysis.py`

LLM node (parallel with fundamental/sentiment), producing macro regime and driver/risk analysis.
Consumes `macro_api` evidence generated by `research` (`MacroDataClient`) plus supplemental context from other evidence.

#### `retry_gate.py`

Consolidates evidence-adequacy retry questions from structural checks, analysis missing fields, and conflict signals.

#### `scenario_debate.py`

Calibrates scenario probabilities and validates full scenario coverage.
On invalid/missing debate output, falls back to baseline probabilities with `debate_flags=["fallback_to_baseline"]`.

#### `report_finalize.py`

Final node — two responsibilities:

1. **Pure-Python validation** (always runs): scenario probability sum, evidence field completeness (`retrieved_at`, `summary`, `reliability` required; `url` optional), claim-to-evidence citation coverage.
2. **LLM Markdown report** via `_llm.complete_text()` (free-form, not JSON mode). Validation errors are appended, and unsupported-claim errors are surfaced as `retry_questions` to trigger a supplementary research retry when budget remains.

### `src/server/services/`

External dependency wrappers — agents do not call third-party APIs directly.

#### `openrouter.py`

OpenRouter LLM client with a four-model free-tier chain:
`openai/gpt-oss-120b:free → qwen/qwen3-next-80b-a3b-instruct:free → meta-llama/llama-3.3-70b-instruct:free → google/gemma-3-27b-it:free`

Per-model retry with exponential backoff on 429/5xx. Two internal error classes:
- `_RetryableError`: retry this model
- `_FatalError`: skip to next model immediately

Public interface:

| Method | Mode | Notes |
|---|---|---|
| `complete(prompt)` | JSON | validates JSON before returning |
| `complete_json(prompt)` | JSON | parses and returns `dict` |
| `complete_text(prompt)` | text | Markdown reports; no JSON enforcement |
| `call_with_retry(prompt, attempts=2)` | JSON | agent-level retry; used by all analysis nodes |

`_headers()` builds auth + optional referer/title headers once per call.

#### `finance_data.py`

yfinance wrapper. Provides: `get_info()`, `get_financials()`, `get_price_history()`, `get_news()`.
`_safe()` converts numpy scalars to Python primitives. `_row()` extracts DataFrame rows safely.

#### `macro_data.py`

Macro data wrapper for FRED + yfinance macro tickers.

- `get_fred_indicators()`: fetches latest FRED series with direction tags; cached 6h
- `get_market_signals()`: fetches VIX/10Y/USD market signals with direction; cached 15m
- `get_all()`: concurrent fetch of both data groups

#### `web_research.py`

Tavily search via httpx. Degrades gracefully (returns `[]`) when `TAVILY_API_KEY` is absent.

#### `cache.py`

SQLite-backed TTL cache with `threading.Lock` and WAL mode.
Operations: `get(key) → object | None`, `set(key, value, ttl_seconds)`, `delete(key)`, `clear_expired()`.
TTL is enforced on read (expired entries return `None`).

#### `collector.py`

Per-request LLM telemetry collector used by `OrchestratorAgent` and `OpenRouterClient`.

- `record(call)`: append + enqueue `LLMCall` events
- `wait_next()`: async dequeue for streaming `llm_call` events
- `all()`: snapshot for final `ResearchResponse.llm_calls`

### `src/server/utils/`

Stateless helpers.

- `status.py`: `initial_agent_statuses()` and `update_status()` — build and mutate `list[AgentStatus]` without in-place mutation
- `validation.py`: `validate_scenario_scores()`, `validate_evidence_completeness()`, `validate_claim_coverage()`

### `src/frontend/`

React-in-HTML frontend without a build step (React + ReactDOM + Babel loaded from CDN).

- `index.html`: page layout + inline `type="text/babel"` app logic; submits query and consumes SSE stream (`agent_status`, `llm_call`, `final`, `error`, `done`)
- `static/styles.css`: stylesheet