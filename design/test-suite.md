# Test Suite

Current repository test inventory is branch-dependent. Use `PYTHONPATH=. pytest tests/ --collect-only -q` as the source of truth.
At the time of this update, the full suite execution result is **130 passed**.

- Unit tests (fast): `PYTHONPATH=. pytest tests/unit/ -q`
- Integration tests: `PYTHONPATH=. pytest tests/integration/ -q`
- Full suite: `PYTHONPATH=. pytest tests/ -q`

> Note: local execution depends on `pytest` and project dependencies being installed in your environment.

---

## Integration

### `tests/integration/test_research_api.py` — 3 tests

FastAPI endpoint tests via `TestClient` (with mocked LLM client wiring). Covers:

- `GET /health` liveness response
- `POST /research` returns a valid response payload (report, scenarios, validation)

---

## Unit

### `tests/unit/test_cache.py` — 11 tests

`Cache` (SQLite backend) behavior:

- set/get roundtrip over multiple value types
- missing key returns `None`
- expiry semantics (`ttl=0`, delete-on-read for expired rows)
- overwrite semantics (value replacement, TTL reset)
- delete / delete missing key safety
- `clear_expired()` count and non-expired preservation
- default TTL behavior

---

### `tests/unit/test_finance_data.py` — 27 tests

`FinanceDataClient` with mocked `yfinance.Ticker`:

- helper functions `_safe`, `_pct`, `_growth` (including edge cases)
- `get_info()` normal path, unknown ticker, exception path
- `get_financials()` core metrics, YoY/CAGR, missing-field detection, exception fallback shape
- `get_price_history()` key shape, return calculation, empty history handling
- `get_news()` normalization, title filtering, exception handling

---

### `tests/unit/test_fundamental_analysis_node.py` — 9 tests

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

### `tests/unit/test_market_sentiment_node.py` — 9 tests

`market_sentiment_node` with mocked `_llm`:

- typed model output and core sentiment field validity
- claim evidence linkage
- runtime failure behavior on LLM failure and no evidence
- prompt filtering behavior for sentiment-relevant evidence
- agent status passthrough and `agent_questions` generation

---

### `tests/unit/test_openrouter.py` — 11 tests

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

`report_verification_node` with mocked `_llm.complete_text`:

- required report sections and disclaimer text presence
- validation success/failure behavior and warning appending
- runtime failure behavior on LLM failure and missing evidence
- empty status passthrough
- open-question reroute signal when claims reference unknown evidence

---

### `tests/unit/test_research_node.py` — 18 tests

`research_node` and `_detect_conflicts` with mocked finance/web/cache services:

- evidence shape, uniqueness, and `research_pass` increment
- normalized metrics/conflict structure
- news cap and web-result cleaning (dedupe + empty URL filtering)
- no-ticker path behavior and hard-fail when no usable evidence
- resilience to single-service failure vs all-service failure
- pass-based evidence ID offset and price-history metrics inclusion
- reliability-divergence conflict detection

---

### `tests/unit/test_scenario_scoring_node.py` — 12 tests

`scenario_scoring_node` with mocked `_llm`:

- scenario output shape, ordering, and probability sum normalization
- normalization from unscaled/equal raw probabilities
- strict cardinality contract (must be 3-5 scenarios)
- runtime failure behavior on LLM failure and no evidence
- empty status passthrough

---

### `tests/unit/test_validation.py` — 8 tests

Validation utility functions in `src/server/utils/validation.py`:

- scenario probability and required-field validation
- evidence completeness validation (`url` optional)
- claim-to-evidence coverage validation

---

### `tests/unit/test_web_research.py` — 11 tests

`WebResearchClient` with mocked `httpx.AsyncClient`:

- result shape and required fields
- missing API key behavior
- HTTP/network failure handling
- URL filtering and empty-result behavior
- `search_news()` query composition and no-key behavior
