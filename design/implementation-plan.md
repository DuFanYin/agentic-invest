# Implementation Plan — Replacing Dummy Code with Real Functionality

## Current state

Every agent node returns hardcoded placeholder data. The LangGraph graph, state
model, status propagation, streaming route, and validation logic are all real
and working. What is fake:

| File | What is fake |
|---|---|
| `services/finance_data.py` | `NotImplementedError` stub |
| `services/web_research.py` | `NotImplementedError` stub |
| `services/cache.py` | no-op, never stores anything |
| `agents/research.py` | three hardcoded `Evidence` objects + fixed metrics dict |
| `agents/fundamental_analysis.py` | two hardcoded claims + fixed metrics/risks |
| `agents/market_sentiment.py` | two hardcoded claims + fixed sentiment fields |
| `agents/scenario_scoring.py` | fixed score heuristic, no real reasoning |
| `agents/report_verification.py` | template string report, no LLM synthesis |

## Guiding principles

- **Layer by layer, bottom up.** Services before agents. Each layer is testable
  in isolation before the next is built on top.
- **One external dependency at a time.** Add yfinance first (free, no key),
  then Tavily/Serper (news/web), then LLM calls inside agents.
- **Keep every integration point behind a service class.** Agents never call
  external APIs directly — they call a service method. This preserves
  testability with mocks.
- **Never break existing tests.** Each phase ends with `pytest tests/ -q`
  passing. New tests are added alongside new code.
- **Incremental enrichment, not flag day.** After each phase the system
  produces real output for at least one data type; other data types stay as
  graceful fallbacks until their phase is complete.

---

## Phase 1 — Finance data service (`services/finance_data.py`)

**Goal:** replace the `NotImplementedError` stub with real financial data
using `yfinance` (free, no API key needed).

### What to build

```python
class FinanceDataClient:
    def get_price_history(self, ticker: str, period: str = "1y") -> dict
    def get_financials(self, ticker: str) -> dict   # income stmt, balance sheet, cashflow
    def get_info(self, ticker: str) -> dict          # market cap, P/E, sector, description
    def get_news(self, ticker: str) -> list[dict]    # recent headlines from yf.Ticker.news
```

### Normalisation contract

`get_financials` must return a dict with at least:

```json
{
  "ttm":            { "revenue": 0, "gross_margin_pct": 0, "operating_margin_pct": 0, "net_income": 0 },
  "three_year_avg": { "revenue_growth_pct": 0, "operating_margin_pct": 0 },
  "latest_quarter": { "revenue": 0, "eps": 0 }
}
```

Missing fields are set to `null` and recorded in `normalized_data["missing_fields"]`.

### What stays dummy

Web/news search, SEC filings, LLM reasoning.

### New tests

- `tests/unit/test_finance_data.py` — mock `yfinance.Ticker`; assert field
  normalisation, missing-field marking, and ticker-not-found fallback.

### Dependencies to add

```
yfinance
```

---

## Phase 2 — Web/news research service (`services/web_research.py`)

**Goal:** replace the `NotImplementedError` stub with real web search using
the Tavily API (free tier: 1 000 req/month; key stored in `.env`).

### What to build

```python
class WebResearchClient:
    def search(self, query: str, max_results: int = 5) -> list[dict]
    # each result: { title, url, content, published_date, score }

    def search_news(self, ticker: str, days: int = 30) -> list[dict]
    # wraps search with a time-filtered query
```

Fallback: if `TAVILY_API_KEY` is absent, log a warning and return `[]` (so
the system degrades gracefully rather than crashing).

### New `.env` key

```
TAVILY_API_KEY=tvly-...
```

### New tests

- `tests/unit/test_web_research.py` — mock `httpx.Client.post`; assert
  result shape normalisation and empty-key fallback.

### Dependencies to add

```
tavily-python
```

---

## Phase 3 — Cache service (`services/cache.py`)

**Goal:** make the cache actually store and retrieve results so repeated
identical queries skip LLM calls.

### What to build

SQLite-backed cache (no extra infra needed):

```python
class Cache:
    def __init__(self, db_path: str = "cache.db")
    def get(self, key: str) -> object | None    # returns None on miss/expiry
    def set(self, key: str, value: object, ttl_seconds: int = 3600) -> None
```

Key = `sha256(query)`. TTL enforced on read (row deleted if expired).

### New tests

- `tests/unit/test_cache.py` — set/get roundtrip, TTL expiry, miss returns
  `None`.

### Dependencies to add

None (stdlib `sqlite3` + `json`).

---

## Phase 4 — Research node (`agents/research.py`)

**Goal:** replace the three hardcoded `Evidence` objects with real data from
Phase 1 and Phase 2 services.

### What to build

```
research_node(state):
  1. if ticker in intent → FinanceDataClient.get_info + get_financials + get_news
  2. WebResearchClient.search(subjects + query keywords, max=8)
  3. Deduplicate by URL; assign reliability ("high" for finance_api, "medium" for web)
  4. Build Evidence objects from real results
  5. Build normalized_data["metrics"] from FinanceDataClient.get_financials output
  6. Populate normalized_data["missing_fields"] from nulls in metrics dict
```

All three service calls are wrapped in try/except; failures produce an
`Evidence` item with `reliability="low"` and a note in `summary`.

### Interface contract (unchanged)

The node still returns `{ evidence: list[Evidence], normalized_data: dict, ... }`.
No other node changes.

### New tests

- `tests/unit/test_research_node.py` — mock both services; assert evidence
  count ≥ 1, all evidence has `url` + `retrieved_at`, `missing_fields`
  correctly populated.
- `tests/integration/test_research_api.py` — existing tests continue to pass
  with mocked services.

---

## Phase 5 — Fundamental analysis node (`agents/fundamental_analysis.py`)

**Goal:** replace hardcoded claims with LLM-generated structured analysis
grounded in the real evidence and metrics from Phase 4.

### What to build

Single `OpenRouterClient.complete()` call with a structured prompt:

```
System: You are a senior equity analyst. Return JSON only. Schema: { claims[], 
        business_quality, financials, valuation, fundamental_risks[], missing_fields[] }
User:   Evidence summaries + normalized metrics (trimmed to fit context window)
```

Parse and validate the JSON response. On parse failure: retry once, then fall
back to the current hardcoded template with a `missing_fields` warning.

### Key constraints

- Each claim must include `evidence_ids` referencing real evidence IDs from
  the state. The prompt instructs the LLM to bind claims to IDs.
- `missing_fields` is populated from `normalized_data["missing_fields"]` plus
  any fields the LLM explicitly flags.
- No claim is allowed without at least one `evidence_id` — the validation node
  already enforces this.

### New tests

- `tests/unit/test_fundamental_analysis_node.py` — mock `OpenRouterClient`;
  assert all claims have `evidence_ids`, `missing_fields` is a list,
  valuation dict is present.

---

## Phase 6 — Market sentiment node (`agents/market_sentiment.py`)

**Goal:** replace hardcoded sentiment with LLM synthesis over real news evidence.

### What to build

Two inputs drive this node:
1. News evidence items collected in Phase 4 (source_type `"news"` or `"web"`)
2. Price history from `FinanceDataClient.get_price_history` (30-day and
   1-year return, volatility, 52-week high/low)

Single LLM call with schema:

```json
{
  "claims": [],
  "news_sentiment": { "direction": "positive|neutral|negative", "confidence": "high|medium|low" },
  "price_action":   { "trend": "...", "return_30d_pct": 0, "volatility": "..." },
  "market_narrative": { "summary": "...", "crowding_risk": "..." },
  "sentiment_risks": [],
  "missing_fields": []
}
```

### New tests

- `tests/unit/test_market_sentiment_node.py` — mock LLM + finance client;
  assert direction is one of the valid enum values, all sentiment_risks have
  `evidence_ids`.

---

## Phase 7 — Scenario scoring node (`agents/scenario_scoring.py`)

**Goal:** replace the fixed score heuristic with LLM-generated scenarios and
probabilities, followed by hard normalisation.

### What to build

LLM prompt includes:
- The fundamental analysis claims and key metrics
- The market sentiment summary
- The investment horizon from intent

LLM returns:

```json
[
  { "name": "...", "description": "...", "raw_score": 0.3,
    "triggers": [], "signals": [], "evidence_ids": [] },
  ...
]
```

Post-processing (Python, not LLM):
1. Assert `len(scenarios) >= 3`; if fewer returned, pad with "Other" at 0.
2. Normalise `scores = [s / sum(scores)]`.
3. Enforce `abs(sum - 1) < 1e-6`.

This keeps the LLM responsible for reasoning and Python responsible for the
mathematical invariant.

### New tests

- `tests/unit/test_scenario_scoring_node.py` — mock LLM returning 2 scenarios
  (below minimum) and assert padding; mock returning unormalised scores and
  assert normalisation.

---

## Phase 8 — Report & verification node (`agents/report_verification.py`)

**Goal:** replace the template string report with a real LLM-synthesised
Markdown report that reads the full research state.

### What to build

LLM prompt provides the full structured context (intent, evidence summaries,
fundamental analysis claims, sentiment, scenarios) and asks for a Markdown
report following the fixed section order from the design doc:

```
Executive Summary / Company Overview / Key Evidence / Fundamental Analysis /
Market Sentiment / Valuation View / Risk Analysis / Future Scenarios /
Bull-Base-Bear Thesis / What To Watch Next / Sources / Disclaimer
```

After LLM generates the report:
1. Run all existing validation checks (scenario scores, citation coverage,
   evidence completeness) — these are pure Python and stay unchanged.
2. If validation has errors, append a `## Validation Warnings` section to the
   report rather than blocking output.

### New tests

- `tests/unit/test_report_node.py` — mock LLM; assert report contains all
  required section headers, `validation_result.is_valid` reflects real
  error/warning counts.

---

## Phase 9 — LLM prompt hardening (`services/openrouter.py`)

**Goal:** make the LLM client production-grade.

### What to build

- `complete_with_retry(prompt, max_retries=2, backoff=2.0)` — exponential
  backoff on 429/5xx.
- Structured output mode: add `response_format: { type: "json_object" }` to
  the payload (supported by OpenAI-compatible endpoints via OpenRouter).
- Token budget guard: truncate evidence summaries to keep prompt under 12k
  tokens (rough char count; conservative).
- Separate system and user message construction into named methods so prompts
  are unit-testable without an API call.

### New tests

- `tests/unit/test_openrouter.py` — mock `httpx`; assert retry behaviour on
  429, assert `RuntimeError` after exhausting retries, assert JSON validation.

---

## Implementation order and dependency graph

```
Phase 1 (yfinance)
    └── Phase 2 (Tavily)
            └── Phase 3 (Cache)
                    └── Phase 4 (research_node) ← unblocks everything below
                            ├── Phase 5 (fundamental_analysis_node)
                            ├── Phase 6 (market_sentiment_node)
                            │       └── Phase 7 (scenario_scoring_node)
                            │               └── Phase 8 (report_verification_node)
                            └── Phase 9 (openrouter hardening) — can run in parallel with 5-8
```

Phases 5, 6, and 9 can be worked on simultaneously once Phase 4 is done.

---

## What never needs to change

- `src/server/models/` — all Pydantic models stay as-is
- `src/server/models/state.py` — ResearchState TypedDict stays as-is
- `src/server/agents/orchestrator.py` — graph topology, gap_check, routing
- `src/server/utils/validation.py` — citation and score validators
- `src/server/utils/status.py` — agent status helpers
- `src/server/routes/` — HTTP layer
- `src/frontend/` — no frontend changes needed
- All existing passing tests

---

## New `.env` keys by phase

| Phase | Key | Source |
|---|---|---|
| 1 | _(none)_ | yfinance is free/keyless |
| 2 | `TAVILY_API_KEY` | tavily.com free tier |
| all | `OPENROUTER_API_KEY` | already required |
| all | `OPENROUTER_MODEL` | already optional |

---

## Suggested delivery order for a single session

1. Phase 1 + tests → smoke test with a real ticker (`NVDA`)
2. Phase 2 + tests → smoke test web search results
3. Phase 3 + tests
4. Phase 4 + tests → first real end-to-end run: real evidence, still dummy analysis
5. Phase 9 (LLM hardening) — makes Phases 5-8 more robust
6. Phase 5 → first real LLM-grounded claim
7. Phase 6 → real sentiment
8. Phase 7 → real scenario probabilities
9. Phase 8 → real report prose
