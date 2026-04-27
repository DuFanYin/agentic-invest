# Test Suite

Current repository test inventory is branch-dependent. Use `PYTHONPATH=. pytest tests/ --collect-only -q` as the source of truth.
At the time of this update, the suite inventory is **121 tests** (`116 unit + 5 integration`).

- Unit tests (fast): `PYTHONPATH=. pytest tests/unit/ -q`
- Integration tests: `PYTHONPATH=. pytest tests/integration/ -q`
- Full suite: `PYTHONPATH=. pytest tests/ -q`

> Note: local execution depends on `pytest` and project dependencies being installed in your environment.

---

## Integration

### `tests/integration/test_research_api.py` — 5 tests

FastAPI endpoint tests via `TestClient` (with mocked LLM client wiring). Covers:

- `GET /health` liveness response
- `POST /research` returns a valid response payload (report, scenarios, validation)
- `POST /research/stream` emits `final` then `done`
- shutdown stream path emits `error` then `done`
- lifespan startup/shutdown flag transition behavior

---

## Unit

### `tests/unit/test_cache.py` — 8 tests

`Cache` (SQLite backend) behavior:

- set/get roundtrip over multiple value types
- missing key returns `None`
- expiry semantics (`ttl=0`, delete-on-read for expired rows)
- overwrite semantics (value replacement, TTL reset)
- delete safety
- `clear_expired()` count and non-expired preservation
- default TTL behavior
- corrupted JSON row is treated as cache miss and auto-cleaned

---

### `tests/unit/test_finance_data.py` — 13 tests

`FinanceDataClient` with mocked `yfinance.Ticker`:

- `get_info()` normal path, unknown ticker, exception path
- `get_financials()` core metrics, YoY/CAGR, missing-field detection, exception fallback shape
- `get_price_history()` key shape, return calculation, empty history handling
- `get_news()` normalization, title filtering, exception handling

---

### `tests/unit/test_fundamental_analysis_node.py` — 6 tests

`fundamental_analysis_node` with mocked `_llm`:

- typed model output and claim structure invariants
- business/metrics mapping from normalized input
- explicit runtime failure behavior on LLM failure and no evidence (no soft fallback)
- agent status passthrough for empty status list
- `agent_questions` generation based on reported missing fields

---

### `tests/unit/test_intent.py` — 1 test

`_parse_intent` fallback behavior when LLM call fails (returns safe default intent, non-empty `subjects`).

---

### `tests/unit/test_market_sentiment_node.py` — 7 tests

`market_sentiment_node` with mocked `_llm`:

- typed model output and core sentiment field validity
- claim evidence linkage
- runtime failure behavior on LLM failure and no evidence
- prompt filtering behavior for sentiment-relevant evidence
- agent status passthrough and `agent_questions` generation

---

### `tests/unit/test_openrouter.py` — 10 tests

`OpenRouterClient` with mocked `httpx.AsyncClient`:

- JSON fence stripping and JSON mode behavior
- no API key error path
- retry/backoff behavior on retryable failures
- model fallback progression and all-models-exhausted failure
- fatal error skip behavior
- invalid JSON treated as retryable
- custom system prompt and `response_format` payload assertions

---

### `tests/unit/test_report_node.py` — 10 tests

`report_finalize_node` (`src/server/agents/report_finalize.py`) with mocked `_llm.complete_text`:

- required report sections and disclaimer text presence
- validation success/failure behavior and warning capture/appending
- hard-fail behavior on LLM failure and missing evidence
- empty status passthrough
- retry-question reroute signal when claims reference unknown evidence IDs
- quality metrics derive scenario-probability validity from debate-calibrated scenarios

---

### `tests/unit/test_research_node.py` — 13 tests

`research_node` and `_detect_conflicts` with mocked finance/web/cache services:

- evidence shape, uniqueness, and `research_iteration` increment
- normalized metrics/conflict structure
- news cap and web-result cleaning (dedupe + empty URL filtering)
- no-ticker path behavior and hard-fail when no usable evidence
- resilience to single-service failure vs all-service failure
- pass-based evidence ID offset and price-history metrics inclusion
- reliability-divergence conflict detection

---

### `tests/unit/test_scenario_scoring_node.py` — 9 tests

`scenario_scoring_node` with mocked `_llm`:

- scenario output shape, ordering, and probability sum normalization
- normalization from unscaled/equal raw probabilities
- strict cardinality contract (must be 3-5 scenarios)
- runtime failure behavior on LLM failure and no evidence
- empty status passthrough

---

### `tests/unit/test_validation.py` — 4 tests

Validation utility functions in `src/server/utils/validation.py`:

- scenario probability and required-field validation
- evidence completeness validation (`url` optional)
- claim-to-evidence coverage validation

---

### `tests/unit/test_web_research.py` — 7 tests

`WebResearchClient` with mocked `httpx.AsyncClient`:

- result shape and required fields
- missing API key behavior
- HTTP/network failure handling
- URL filtering and empty-result behavior
- `search_news()` query composition and no-key behavior

---

### `tests/unit/test_planning_agent.py` — 5 tests

`plan()` and `make_planning_node()` behavior:

- structured planning output shape (`intent`, `research_focus`, `must_have_metrics`, `plan_notes`)
- fallback behavior on LLM errors / invalid JSON
- fallback enrichment when LLM returns empty planning fields
- node output wiring (`research_iteration=0`, cleared `retry_questions`)

---

### `tests/unit/test_macro_analysis_node.py` — 7 tests

`macro_analysis_node` (`src/server/agents/macro_analysis.py`) with mocked `_llm`:

- typed model output and macro field contracts
- `agent_questions` emission from `missing_fields` (and empty when none)
- behavior with macro-only or supplemental non-macro evidence
- hard-fail behavior for LLM error / malformed payload

---

### `tests/unit/test_scenario_debate_node.py` — 10 tests

`scenario_debate_node` (`src/server/agents/scenario_debate.py`) calibration and fallback behavior:

- calibrated probability sum, adjustment cap constraints, and bad-sum normalization
- fallback to baseline scenarios on missing scenario coverage, LLM failure, bad JSON, or empty input scenarios
- valid output shape for debate summary and adjustment artifacts (including no-adjustment path)

---

### `tests/unit/test_research_retry_gate.py` — 6 tests

`retry_gate_node` / `retry_router` (`src/server/agents/retry_gate.py`) decision logic:

- retry routing when `retry_questions` are present
- clear-after-max-iterations behavior
- ticker/horizon checks only for `scope="company"` (skipped for macro scope)
- typed conflict model handling without crashes
