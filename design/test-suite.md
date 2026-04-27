# Test Suite

**71 tests, all passing.** Run with: `PYTHONPATH=. pytest tests/ -q`

---

## Integration

### `tests/integration/test_research_api.py` — 3 tests

End-to-end HTTP layer. Spins up the FastAPI app via `TestClient` with the full LangGraph graph and mocked OpenRouter. Covers:

- `GET /health` returns 200
- `POST /research` returns a structurally valid response (intent, evidence, scenarios, report present)
- Validation result on a well-formed run reports `is_valid: true`

---

## Unit

### `tests/unit/test_finance_data.py` — 25 tests

`FinanceDataClient` with `yfinance.Ticker` mocked. Covers:

- `_safe()`: numpy float/int conversion, NaN → None
- `_pct()` / `_growth()`: division, zero-denominator, None inputs
- `get_info()`: profile fields returned, unknown ticker (no `shortName`) → empty dict, exception → empty dict
- `get_financials()`: TTM revenue, gross margin, operating margin, net income; YoY and 3-year CAGR; latest-quarter EPS; `missing_fields` populated when a row is NaN; full exception → empty fallback dict
- `get_price_history()`: return keys present, 1y return calculation, empty DataFrame → error dict
- `get_news()`: normalised shape, items without title skipped, exception → empty list

---

### `tests/unit/test_openrouter.py` — 16 tests

`OpenRouterClient` with `httpx.Client` mocked. Covers:

- `_strip_fences()`: ` ```json ``` ` block, plain ` ``` ``` ` block, passthrough when no fence
- `complete()`: success returns valid JSON string, markdown fences stripped automatically, raises `RuntimeError` when `OPENROUTER_API_KEY` absent
- Retry logic: retries on 429 then succeeds; exhausts retries on one model then falls back to next model; raises `RuntimeError` when all models exhausted
- Fatal errors: 400 skips to next model immediately (no retries)
- Invalid JSON from model treated as retryable
- Model selection: free chain used when no env model set; explicit model kwarg respected
- Payload shape: custom system prompt sent in messages; `response_format: json_object` always present

---

### `tests/unit/test_research_node.py` — 18 tests

`research_node` with `FinanceDataClient` mocked. Covers:

- Output shape: evidence list returned, all items have `id`/`source_type`/`summary`/`retrieved_at`, IDs unique
- `normalized_data`: has `metrics` with TTM / 3-year-avg / latest-quarter keys; `missing_fields` is a list
- `research_pass` incremented on each call (pass 0 → 1, pass 1 → 2)
- Source types: `financial_api` evidence present; `news` evidence present; news capped at 5 items
- `missing_fields` from financials propagated into `normalized_data`
- No-ticker path: falls back to a single `web` evidence item
- Resilience: each of `get_info`, `get_financials`, `get_price_history` can fail independently without crashing; all four failing together still returns fallback evidence
- Multi-pass ID offset: second-pass evidence IDs start at ≥ 100
- Price history stored under `metrics["price_history"]`

---

### `tests/unit/test_scenario_scoring.py` — 2 tests

`scenario_scoring_node` output invariants (dummy node, real checks):

- Scenario scores sum to 1.0 (within float tolerance)
- At least 3 scenarios returned

---

### `tests/unit/test_validation.py` — 6 tests

`src/server/utils/validation.py` pure functions. Covers:

- `check_scenario_scores`: passes when sum ≈ 1, returns error when not
- `check_evidence_completeness`: passes when all fields present, fails when URL missing
- `check_claim_coverage`: passes with valid evidence IDs, fails with an unknown ID

---

### `tests/unit/test_intent.py` — 1 test

`parse_intent` fallback path: when the LLM call raises, the node returns a default `ResearchIntent` rather than propagating the exception.
