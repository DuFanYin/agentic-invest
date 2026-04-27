# Test Suite

**176 tests, all passing.**

- Unit tests only (fast): `PYTHONPATH=. pytest tests/unit/ -q`
- Full suite including integration (slow, ~50s — hits real OpenRouter API): `PYTHONPATH=. pytest tests/ -q`

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

### `tests/unit/test_research_node.py` — 27 tests

`research_node` and `_detect_conflicts` with `FinanceDataClient`, `WebResearchClient`, and `Cache` mocked. Covers:

- Output shape: evidence list returned, all items have required fields, IDs unique
- `normalized_data`: metrics with TTM / 3-year-avg / latest-quarter; `missing_fields` is a list; `conflicts` is a list
- `research_pass` incremented each call
- Source types: `financial_api`, `news`, and `web` evidence all present; news capped at 5 items
- Web search: called on every run; duplicate URLs across yfinance and web results deduplicated; empty-URL web results excluded
- `missing_fields` from financials propagated
- No-ticker path: web search still runs; returns fallback when both finance and web return nothing
- Resilience: `get_info`, `get_financials`, `get_price_history`, web search each fail independently without crash; all failing together still returns fallback
- Multi-pass ID offset; price history in `metrics["price_history"]`
- `_detect_conflicts`: no conflicts when all evidence same reliability; conflict detected when high + low reliability sources cover the same topic; `conflicts` key always present in `normalized_data`

---

### `tests/unit/test_cache.py` — 17 tests

`Cache` using a `tmp_path` SQLite file per test. Covers:

- Set/get roundtrip for string, dict, list, int, and None values
- Miss on unknown key returns None
- TTL expiry: `ttl=0` entry returns None after a short sleep; non-expired entry returned normally; expired row is deleted from DB on read
- Overwrite: second `set` replaces value and resets TTL
- Explicit `delete()`: key gone; deleting non-existent key is safe
- `clear_expired()`: removes expired rows, returns count; returns 0 when nothing expired
- Default TTL respected when `ttl_seconds` not passed
- Multiple independent keys do not interfere

---

### `tests/unit/test_web_research.py` — 15 tests

`WebResearchClient` with `httpx.Client` mocked. Covers:

- Result shape: list returned, required fields present (`title`, `url`, `content`, `retrieved_at`), `published_date` and `score` passed through
- Missing API key → returns `[]` without making any HTTP call
- HTTP errors: 401, 429, 500 all return `[]`
- Network failures: timeout and network error both return `[]`
- URL filtering: results missing or with empty `url` are dropped
- Empty API response → `[]`
- `search_news()`: ticker name present in the outgoing query payload; respects missing key; result shape matches `search()`

---

### `tests/unit/test_report_node.py` — 16 tests

`report_verification_node` with `_llm.complete_text` mocked. Covers:

- Output keys: `report_markdown`, `report_json`, `validation_result`, `open_questions` all present
- All 12 required section headers present in LLM-generated report; Disclaimer contains "Not financial advice"
- Valid state → no errors, `is_valid: true`
- Validation errors appended as `## Validation Warnings` section in report
- Missing fields in FA/sentiment produce warnings on `validation_result`
- Fallback when LLM raises: template report contains all required sections
- Fallback when no evidence: still returns a report
- `report_json` scenarios and evidence are dicts with required keys
- Empty statuses returned unchanged
- `open_questions` empty when all claims cite known evidence IDs
- `open_questions` populated (prefixed `"report_verification:"`) when a claim references an unknown evidence ID

---

### `tests/unit/test_fundamental_analysis_node.py` — 19 tests

`fundamental_analysis_node` with `_llm` mocked. Covers:

- Output shape: `fundamental_analysis` key present; claims list; all claims cite evidence IDs and have valid confidence
- `business_quality`, `valuation`, `fundamental_risks` present; `missing_fields` is a list
- Metrics attached from state; `_llm_used: true` on success
- Fallback when LLM raises: `_llm_used: false`, all required keys present
- Fallback when no evidence
- `missing_fields` from state propagated into fallback
- Empty statuses returned unchanged
- `agent_questions` empty when LLM reports no missing fields
- `agent_questions` populated (prefixed `"fundamental_analysis needs:"`) when LLM reports missing fields
- `agent_questions` empty on fallback (LLM not used)

---

### `tests/unit/test_market_sentiment_node.py` — 18 tests

`market_sentiment_node` with `_llm` mocked. Covers:

- Output shape: `market_sentiment` key present; claims list; all claims cite evidence IDs
- `news_sentiment.direction` is one of `positive/neutral/negative`; `price_action` and `market_narrative` present
- `sentiment_risks` is a list; `missing_fields` is a list; `_llm_used: true` on success
- Fallback when LLM raises: `direction == "neutral"`, `_llm_used: false`, all required keys present
- Fallback when no evidence
- Prompt includes all evidence IDs (not just news type)
- Empty statuses unchanged
- `agent_questions` empty when LLM reports no missing fields
- `agent_questions` populated (prefixed `"market_sentiment needs:"`) when LLM reports missing fields
- `agent_questions` empty on fallback

---

### `tests/unit/test_scenario_scoring_node.py` — 13 tests

`scenario_scoring_node` with `_llm` mocked. Covers:

- Output: `scenarios` key; at least 3 scenarios; scores sum to 1.0; all scores non-negative; all scenarios have evidence IDs, name, description
- Normalisation: raw weights `[3, 5, 2]` → `[0.3, 0.5, 0.2]`; equal weights → thirds
- Padding: LLM returning 2 scenarios padded to 3; 1 scenario padded to 3; scores still sum to 1
- Fallback when LLM raises: still 3 scenarios, scores sum to 1
- Fallback when no evidence; empty statuses unchanged

---

### `tests/unit/test_scenario_scoring.py` — 2 tests

`scenario_scoring_node` output invariants (dummy node, real checks):

- Scenario scores sum to 1.0 (within float tolerance)
- At least 3 scenarios returned

---

### `tests/unit/test_validation.py` — 6 tests

`src/server/utils/validation.py` pure functions. Covers:

- `validate_scenario_scores`: passes when sum ≈ 1, returns error when not
- `validate_evidence_completeness`: passes when all required fields present; passes when `url` is None (it's optional); fails when `retrieved_at`, `summary`, or `reliability` missing
- `validate_claim_coverage`: passes with valid evidence IDs, fails with an unknown ID

---

### `tests/unit/test_intent.py` — 1 test

`_parse_intent` fallback path: when the LLM call raises, the function returns a default `ResearchIntent` with a non-empty `subjects` list rather than propagating the exception.
