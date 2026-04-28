# Test Suite

Current repository test inventory is branch-dependent. Use `PYTHONPATH=. pytest tests/ --collect-only -q` as the source of truth.
At the time of this update, the suite inventory is **126 tests** (`121 unit + 5 integration`).

- Unit tests (fast): `PYTHONPATH=. pytest tests/unit/ -q`
- Integration tests: `PYTHONPATH=. pytest tests/integration/ -q`
- Full suite: `PYTHONPATH=. pytest tests/ -q`

> Note: local execution depends on `pytest` and project dependencies being installed in your environment.

---

## Integration

### `tests/integration/test_research_api.py` — 5 tests

FastAPI endpoint tests via `TestClient` (with mocked LLM wiring). Covers:

- `GET /health` readiness (`200` when LLM key is present, otherwise `503`)
- `POST /research` payload contract
- `POST /research/stream` final/done flow
- stream error path
- lifespan startup/shutdown behavior

---

## Unit

### `tests/unit/test_cache.py` — 6 tests

`Cache` core behavior:

- set/get roundtrip
- expiry semantics
- overwrite semantics
- delete behavior
- `clear_expired()` behavior
- default TTL behavior

---

### `tests/unit/test_finance_data.py` — 5 tests

`FinanceDataClient` smoke and failure coverage:

- `get_info()` core field mapping
- `get_financials()` core metric consistency
- missing-field detection on NaN financial inputs
- exception fallback shape for financials
- price-history return calculation

---

### `tests/unit/test_fundamental_analysis_node.py` — 3 tests

`fundamental_analysis_node` core behavior:

- typed output + metrics pass-through
- degraded fallback on LLM exhaustion
- fail-fast on missing evidence

---

### `tests/unit/test_intent.py` — 1 test

Intent fallback when planning LLM is unavailable.

---

### `tests/unit/test_macro_analysis_node.py` — 2 tests

`macro_analysis_node`:

- typed output smoke
- degraded fallback on LLM exhaustion

---

### `tests/unit/test_market_sentiment_node.py` — 3 tests

`market_sentiment_node`:

- typed output smoke
- degraded fallback on LLM exhaustion
- fail-fast on missing evidence

---

### `tests/unit/test_node_contracts.py` — 44 tests

Contract enforcement and topology sanity:

- contract table completeness
- declared/undeclared read-write enforcement
- global read/write allowances
- key handoff sanity checks between nodes

---

### `tests/unit/test_llm_provider.py` — 8 tests

`LLMClient` resilience-focused behavior:

- JSON parse path + markdown fence stripping
- no API key failure
- retry on `429`
- model fallback and all-models-exhausted failure
- fatal `400` skip behavior
- payload shape (`system`, `response_format`)

---

### `tests/unit/test_planning_agent.py` — 3 tests

`plan()` and planning node wiring:

- structured planning output
- fallback when LLM fails
- node output contract (`intent`, `plan_context`, reset fields)

---

### `tests/unit/test_report_node.py` — 4 tests

`report_finalize_node`:

- required section rendering
- validation error append behavior
- degraded report output on LLM failure
- fail-fast on missing evidence

---

### `tests/unit/test_research_node.py` — 7 tests

`research_node` and `_detect_conflicts`:

- smoke contract + normalized metrics
- no-ticker/no-web hard-fail path
- single-service failure tolerance
- all-services-fail hard-fail path
- reliability-divergence conflict detection

---

### `tests/unit/test_llm_judge.py` — 5 tests

`llm_judge_node` / `llm_judge_router`:

- retry routing when questions exist
- clear-after-max-iterations
- structural-gap retry generation
- analysis-robustness retry generation
- conflict-based retry generation

---

### `tests/unit/test_scenario_debate_node.py` — 4 tests

`scenario_debate_node`:

- typed output smoke
- calibrated probabilities sum check
- fallback on all-advocates failure
- fallback on arbitrator failure

---

### `tests/unit/test_scenario_scoring_node.py` — 4 tests

`scenario_scoring_node`:

- scenario output + normalized probability sum
- strict cardinality failure path (<3)
- fail-fast on LLM error
- fail-fast on missing evidence

---

### `tests/unit/test_validation.py` — 5 tests

Validation utilities:

- scenario score required-field/probability validation
- evidence completeness required-field validation
- unknown evidence-id claim coverage validation

---

### `tests/unit/test_web_research.py` — 9 tests

`WebResearchClient`:

- result shape smoke
- missing API key behavior
- HTTP error handling
- network failure handling
- URL filtering behavior
