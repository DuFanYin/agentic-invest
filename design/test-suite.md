# Test Suite

Run tests:

- Unit: `PYTHONPATH=. pytest tests/unit/ -q`
- Integration: `PYTHONPATH=. pytest tests/integration/ -q`
- Full suite: `PYTHONPATH=. pytest tests/ -q`

---

## Integration

### `tests/integration/test_research_api.py`

FastAPI endpoint tests via `TestClient` (with mocked LLM wiring). Covers:

- `GET /health` readiness (`200` when LLM key is present, otherwise `503`)
- `POST /research` payload contract
- `POST /research/stream` final/done flow
- stream error path
- lifespan startup/shutdown behavior

---

## Unit

### `tests/unit/test_contracts.py`

Registry ↔ contract ↔ graph topology consistency and contract enforcement (merged from `test_node_contracts` and `test_registry_consistency`):

- registry covers all 9 graph nodes; `NODE_CONTRACTS` mirrors registry exactly
- valid failure modes; capability deps only on data-fetching nodes
- parallel group members and their failure modes
- data-flow topology sanity (evidence, scenarios, plan_context flows)
- `depends_on` no self-reference and no unknown agents
- declared/undeclared read and write enforcement (parametrized per node)
- global field allowances (`agent_statuses`)

---

### `tests/unit/test_judge_policy.py`

Policy engine rules, priority ordering, and `llm_judge_router_fn` (merged from `test_policy_engine` and `test_policy_router`):

- all 5 policy rules: iteration limit, structural gap, all-degraded halt, evidence conflict, analysis robustness, default continue
- priority ordering: iteration limit beats structural; all-degraded beats evidence conflict
- router: retry actions route to `research`, continue to `scenario_scoring`, halt to `report_finalize`, missing decision defaults to `scenario_scoring`

---

### `tests/unit/test_llm_provider.py`

`LLMClient` resilience:

- markdown fence stripping, no API key failure
- retry on `429`, model fallback, all-models-exhausted failure
- fatal `400` skip behavior
- `call_with_retry` simplified-prompt retry on invalid JSON; both-fail propagation; non-JSON errors bypass retry
- payload shape (`system`, `response_format`)

---

### `tests/unit/test_web_research.py`

`WebResearchClient`: result shape, missing API key, HTTP errors, network failures, URL filtering.

---

### `tests/unit/test_research_node.py`

`research_node` and `detect_conflicts`:

- smoke contract + normalized metrics shape
- no-ticker/no-web hard-fail path
- single-service failure tolerance (parametrized)
- all-services-fail hard-fail path
- LLM-planned multi-query web fetch
- fallback to deterministic queries when LLM fails
- reliability-divergence conflict detection

---

### `tests/unit/test_llm_judge.py`

`llm_judge_node` behavior:

- skips LLM at iteration cap; skips even when conflicts present (regression)
- structural gap retry when ticker missing
- conflict-based retry with two LLM calls
- analysis-robustness retry from first judge call
- halt routing when all analyses degraded
- degraded path when LLM fails

---

### `tests/unit/test_report_node.py`

`report_finalize_node`:

- validation errors appended to report
- LLM failure degrades to placeholder sections
- fail-fast on missing evidence
- single degraded node produces warning; all three degraded raises
- degraded debate produces warning

---

### `tests/unit/test_cache.py`

`Cache`: set/get roundtrip, expiry, overwrite, explicit delete, `clear_expired()`, default TTL.

---

### `tests/unit/test_analysis_gate_context.py`

Gate-context copy injected into FA / macro / sentiment prompts (`analysis_gate_context_for_prompt`) on supplemental research batches.

---

### `tests/unit/test_analysis_nodes.py`

`fundamental_analysis_node`, `macro_analysis_node`, `market_sentiment_node` (merged):

- fundamental: happy path returns typed output, not degraded; degrades on missing evidence or LLM failure (parametrized)
- macro: degrades on LLM failure
- sentiment: degrades on missing evidence or LLM failure (parametrized)

---

### `tests/unit/test_validation.py`

Validation utilities: scenario score, evidence completeness, claim coverage.

---

### `tests/unit/test_finance_data.py`

`FinanceDataClient`: info field mapping, financials metrics, missing-field detection, exception fallback, price-history return calculation.

---

### `tests/unit/test_scenario_scoring_node.py`

`scenario_scoring_node`: output shape + probability sum, cardinality failure (<3), fail-fast on LLM error or missing evidence (parametrized).

---

### `tests/unit/test_prompt_builder.py`

Prompt assembly guardrails: leading newline preservation, schema shape, query injection, block ordering.

---

### `tests/unit/test_planning_agent.py`

`plan()` and `make_planning_node()`: structured output shape, fallback on LLM failure, custom section injection, node state contract.

---

### `tests/unit/test_scenario_debate_node.py`

`scenario_debate_node`: happy path shape + probability sum; degrades when all advocates or arbitrator fails (parametrized).

---

### `tests/unit/test_intent.py`

Intent fallback when planning LLM is unavailable.
