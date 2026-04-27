# Codebase Structure

## 1) Goal

The repository is organised by backend services, frontend pages, core agent system, shared models, tests, and docs.
The architecture is layered: routes → agents → services, with a shared model and utils layer beneath them all.

## 2) Folder Structure

```text
agentic-invest/
├── design/
│   ├── core-agent-system.md
│   ├── implementation-plan.md
│   ├── test-suite.md
│   ├── frontend.md
│   ├── peripheral-plan.md
│   └── codebase-structure.md
├── src/
│   ├── server/
│   │   ├── config.py                  ← central env/config (loaded once)
│   │   ├── main.py
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
│   │   │   ├── state.py               ← LangGraph shared state
│   │   │   ├── evidence.py
│   │   │   ├── intent.py
│   │   │   └── scenario.py
│   │   ├── agents/
│   │   │   ├── __init__.py
│   │   │   ├── orchestrator.py
│   │   │   ├── research.py
│   │   │   ├── fundamental_analysis.py
│   │   │   ├── market_sentiment.py
│   │   │   ├── scenario_scoring.py
│   │   │   └── report_verification.py
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── openrouter.py
│   │   │   ├── finance_data.py
│   │   │   ├── web_research.py
│   │   │   └── cache.py
│   │   └── utils/
│   │       ├── __init__.py
│   │       ├── status.py              ← AgentStatus mutation helpers
│   │       └── validation.py
│   └── frontend/
│       ├── index.html
│       └── static/
│           └── styles.css
├── tests/
│   ├── unit/
│   │   ├── test_cache.py
│   │   ├── test_finance_data.py
│   │   ├── test_fundamental_analysis_node.py
│   │   ├── test_intent.py
│   │   ├── test_market_sentiment_node.py
│   │   ├── test_openrouter.py
│   │   ├── test_report_node.py
│   │   ├── test_research_node.py
│   │   ├── test_scenario_scoring.py
│   │   ├── test_scenario_scoring_node.py
│   │   ├── test_validation.py
│   │   └── test_web_research.py
│   └── integration/
│       └── test_research_api.py
└── outputs/
    └── sample-report.md
```

## 3) Directory Responsibilities

### `design/`

Design documentation only — not imported at runtime.

- `core-agent-system.md`: multi-agent architecture, graph topology, data contracts
- `implementation-plan.md`: phased delivery plan with completion status
- `test-suite.md`: per-file test coverage summary and run commands
- `frontend.md`: React-in-HTML UI layout, SSE wiring, and section reveal behavior
- `peripheral-plan.md`: peripheral architecture and integration planning
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

All services import from here — no scattered `os.getenv` calls elsewhere.

### `src/server/main.py`

Creates the FastAPI application, mounts static files, and registers route blueprints.
Exposes: `/` (frontend), `/health`, `/research`, `/research/stream`.

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
| `analysis.py` | typed analysis payloads (`FundamentalAnalysis`, `MarketSentiment`, `NormalizedData`) |
| `finance.py` | typed finance service payload contracts |
| `state.py` | `ResearchState` (LangGraph `TypedDict`) with annotated reducers |
| `evidence.py` | `Evidence` — `url` is `Optional[str]` |
| `intent.py` | `ResearchIntent` |
| `scenario.py` | `Scenario` — `probability: float` in `[0, 1]` |

`ResearchState` reducer notes:
- `evidence`: `operator.add` — parallel passes accumulate items
- `agent_questions`: `_accumulate_or_reset` — parallel analysis nodes append missing-field questions; `gap_check` clears via sentinel
- `open_questions`: plain replace — gap_check resets each cycle
- `agent_statuses`: `_last_list` custom reducer — merges concurrent writes by agent and prefers newer `last_update_at` snapshots

### `src/server/agents/`

LangGraph node functions and the graph wiring.

#### `orchestrator.py`

Builds and owns the `StateGraph`. Graph topology:

```
START → parse_intent → research → [parallel] → gap_check ─(gaps?)─→ research (retry, ≤2 passes)
                                   fundamental_analysis              └─(no gaps)─→ scenario_scoring
                                   market_sentiment                                → report_verification
                                                                                └─(unsupported claims + retry budget)→ research
                                                                                └─(otherwise)→ END
```

- `_make_parse_intent_node(llm_client)`: calls `_parse_intent()` to extract `ResearchIntent` from the raw query
- `_gap_check_node`: merges structural gaps (ticker/horizon), agent-raised questions, and research conflict signals; clears `open_questions` after `_MAX_RESEARCH_PASSES = 2`
- `_gap_router`: returns `"research"` or `"scenario_scoring"`
- `build_graph(llm_client)`: compiles the graph per request execution
- `OrchestratorAgent.run()`: async invoke (`graph.ainvoke`)
- `OrchestratorAgent.run_stream()`: yields `agent_status`, `llm_call`, and `final` domain events (route layer wraps with SSE `event` names, plus `error`/`done` control events)

#### `research.py`

Collects evidence from two external sources with in-process SQLite caching:

| Source | TTL | Data |
|---|---|---|
| `FinanceDataClient` | 3600 s | company info, financials, price history, yfinance news |
| `WebResearchClient` | 900 s | Tavily web search results |

Cache keys are `sha256(prefix + parts)[:16]`. Deduplicates web URLs. If no usable evidence can be collected, the node raises a runtime error (no synthetic evidence fallback).

Returns: `evidence` (list appended via reducer), `normalized_data` (metrics, missing_fields, open_question_context), incremented `research_pass`.

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

LLM node (sequential, after gap_check passes).
LLM returns raw weights (`raw_probability`); Python normalises to `sum(probability) = 1` before constructing `Scenario` objects.
Requires 3-5 scenarios from the model; out-of-range counts are treated as invalid output.
If scenario generation fails, the node raises a runtime error (no stub scenario fallback).

#### `report_verification.py`

Final node — two responsibilities:

1. **Pure-Python validation** (always runs): scenario probability sum, evidence field completeness (`retrieved_at`, `summary`, `reliability` required; `url` optional), claim-to-evidence citation coverage.
2. **LLM Markdown report** via `_llm.complete_text()` (free-form, not JSON mode). Requires all 12 named sections. If generation fails, the node raises a runtime error (no template fallback). Validation errors are appended as `## Validation Warnings`, and unsupported-claim errors are surfaced as `open_questions` to trigger a supplementary research retry when budget remains.

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

#### `web_research.py`

Tavily search via httpx. Degrades gracefully (returns `[]`) when `TAVILY_API_KEY` is absent.

#### `cache.py`

SQLite-backed TTL cache with `threading.Lock` and WAL mode.
Operations: `get(key) → dict | None`, `set(key, value, ttl_seconds)`, `delete(key)`, `clear_expired()`.
TTL is enforced on read (expired entries return `None`).

### `src/server/utils/`

Stateless helpers.

- `status.py`: `initial_agent_statuses()` and `update_status()` — build and mutate `list[AgentStatus]` without in-place mutation
- `validation.py`: `validate_scenario_scores()`, `validate_evidence_completeness()`, `validate_claim_coverage()`

### `src/frontend/`

React-in-HTML frontend without a build step (React + ReactDOM + Babel loaded from CDN).

- `index.html`: page layout + inline `type="text/babel"` app logic; submits query and consumes SSE stream (`agent_status`, `llm_call`, `final`, `error`, `done`)
- `static/styles.css`: stylesheet

### `tests/`

Two-tier test suite (see `design/test-suite.md` for current inventory and counting method).

**Unit tests** (`tests/unit/`) — fast, no network, all LLM/HTTP calls mocked:

| File | Covers |
|---|---|
| `test_cache.py` | SQLite TTL cache, concurrency |
| `test_finance_data.py` | yfinance data normalisation |
| `test_fundamental_analysis_node.py` | LLM prompt construction, fallback |
| `test_intent.py` | `_parse_intent` fallback when LLM unavailable |
| `test_market_sentiment_node.py` | sentiment prompt, evidence filtering |
| `test_openrouter.py` | model chain, retry, JSON validation, error classes |
| `test_report_node.py` | report generation, validation error appending |
| `test_research_node.py` | cache wiring, evidence assembly, web dedup |
| `test_scenario_scoring.py` | probability normalisation, sum-to-1 property |
| `test_scenario_scoring_node.py` | LLM parse, padding, fallback |
| `test_validation.py` | probability sum, evidence completeness, claim coverage |
| `test_web_research.py` | Tavily search, graceful degradation |

**Integration tests** (`tests/integration/`) — API-level flow tests with mocked LLM wiring:

| File | Covers |
|---|---|
| `test_research_api.py` | full `/research` request → Markdown report end-to-end |

**Run commands:**

```bash
# Unit tests only (fast)
pytest tests/unit/

# Full suite including integration
pytest

# Single file
pytest tests/unit/test_scenario_scoring.py -v
```
