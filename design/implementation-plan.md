# Implementation Plan — Replacing Dummy Code with Real Functionality

## Current state — all phases complete ✅

All agent nodes and services are real. The system produces genuine output end-to-end.

| File | Status |
|---|---|
| `services/finance_data.py` | ✅ Real yfinance integration |
| `services/web_research.py` | ✅ Real Tavily search (degrades gracefully if key absent) |
| `services/cache.py` | ✅ SQLite TTL cache |
| `services/openrouter.py` | ✅ Free model chain with retry/fallback |
| `agents/research.py` | ✅ Real evidence from yfinance + Tavily |
| `agents/fundamental_analysis.py` | ✅ LLM-grounded claims over real evidence |
| `agents/market_sentiment.py` | ✅ LLM synthesis over news + price history |
| `agents/scenario_scoring.py` | ✅ LLM scenarios, Python normalisation |
| `agents/report_verification.py` | ✅ LLM Markdown report, Python validation |

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

## ~~Phase 1 — Finance data service~~ ✅ DONE

**Delivered:** `services/finance_data.py` — real yfinance integration; `services/openrouter.py` — free model fallback chain with retry/backoff.

- `FinanceDataClient`: `get_info`, `get_financials`, `get_price_history`, `get_news` — all backed by yfinance, numpy-safe, missing-field tracked
- `OpenRouterClient`: free model chain (`gpt-oss-20b → gpt-oss-120b → nemotron-3-super`), `_RetryableError`/`_FatalError` distinction, `response_format: json_object`, `_strip_fences`, `_load_env` directory walk
- 53 unit tests passing (`tests/unit/test_finance_data.py`, `tests/unit/test_openrouter.py`)
- `requirements.txt`: added `yfinance`, `python-dotenv`

---

## ~~Phase 2 — Web/news research service~~ ✅ DONE

**Delivered:** `services/web_research.py` — Tavily search client over `httpx` (no extra SDK needed).

- `search(query, max_results)`: posts to Tavily API, normalises results to `{title, url, content, published_date, score, retrieved_at}`, filters out items without URL
- `search_news(ticker, days)`: wraps `search` with a ticker-focused query
- Degrades gracefully to `[]` on missing `TAVILY_API_KEY`, any HTTP error, timeout, or network failure
- `_load_env()` walks up directory tree same as OpenRouter client
- 15 new tests in `tests/unit/test_web_research.py` — 86 total passing

---

## ~~Phase 3 — Cache service~~ ✅ DONE

**Delivered:** `services/cache.py` — SQLite-backed TTL cache, no extra dependencies.

- `get(key)`: returns cached value or None on miss/expiry; deletes expired row on read
- `set(key, value, ttl_seconds)`: upserts with configurable TTL (default 3600s); JSON-serialises any value
- `delete(key)`: explicit removal
- `clear_expired()`: batch cleanup, returns count deleted
- WAL mode + per-instance threading lock for safety
- 17 tests in `tests/unit/test_cache.py` — 103 total passing

---

## ~~Phase 4 — Research node~~ ✅ DONE

**Delivered:** `agents/research.py` — replaced all dummy evidence with real yfinance data.

- Calls `get_info`, `get_financials`, `get_price_history`, `get_news` when a ticker is in intent
- Builds typed `Evidence` objects per source (`financial_api`, `news`); news capped at 5
- `normalized_data["metrics"]` has real TTM / 3-year-avg / latest-quarter / price-history
- `missing_fields` propagated from `FinanceDataClient`
- Each service call is independently try/excepted; all failing → graceful `web` fallback evidence
- ID offset by `pass_id * 100` so multi-pass evidence IDs never collide
- 18 new tests in `tests/unit/test_research_node.py` — 71 total passing

---

## ~~Phase 5 — Fundamental analysis node~~ ✅ DONE

**Delivered:** `agents/fundamental_analysis.py` — LLM-grounded claims over real evidence and metrics.

- Builds a structured prompt with all evidence IDs + summaries, TTM/3yr/quarter metrics, missing fields, and intent
- Single `OpenRouterClient.complete()` call; retries once on parse failure; falls back to stub on full failure
- LLM must cite evidence IDs in every claim and risk — enforced by prompt schema
- Attaches `metrics` and `_llm_used` flag to output; `missing_fields` propagated from state
- 16 tests in `tests/unit/test_fundamental_analysis_node.py` — 119 total passing

---


---

## ~~Phase 6 — Market sentiment node~~ ✅ DONE

**Delivered:** `agents/market_sentiment.py` — LLM synthesis over news evidence and price history.

- Filters evidence to `news`/`web` source types for the prompt; passes all evidence IDs for citation
- Price history from `normalized_data["metrics"]["price_history"]` included in prompt
- LLM returns `claims`, `news_sentiment` (direction must be positive/neutral/negative), `price_action`, `market_narrative`, `sentiment_risks`, `missing_fields`
- Retries once on failure; falls back to neutral stub with `_llm_used: false`
- 15 tests in `tests/unit/test_market_sentiment_node.py`

---

## ~~Phase 7 — Scenario scoring node~~ ✅ DONE

**Delivered:** `agents/scenario_scoring.py` — LLM generates 3 scenarios, Python owns normalisation.

- LLM returns `raw_score` weights (not required to sum to 1); parser normalises before constructing `Scenario` objects (avoids Pydantic `le=1` validation error on raw weights > 1)
- Pads to minimum 3 scenarios if LLM returns fewer; padding adds score=0 then re-normalises
- Fallback: equal-weight bull/base/bear if LLM fails or no evidence
- Score invariant: `sum(scores) == 1.0` guaranteed by Python, never by LLM
- 13 tests in `tests/unit/test_scenario_scoring_node.py` — 147 total passing

---

## ~~Phase 8 — Report & verification node~~ ✅ DONE

**Delivered:** `agents/report_verification.py` — LLM writes full Markdown report, Python validates.

- `_llm_markdown()`: calls OpenRouter without `json_object` mode (report is prose, not JSON); own model fallback chain; bypasses `OpenRouterClient` which enforces JSON mode
- Prompt provides full context: intent, all evidence summaries, metrics JSON, FA claims/risks, sentiment, scenarios
- Validation (scenario scores, evidence completeness, claim coverage) always runs in pure Python after LLM
- Errors appended as `## Validation Warnings` section rather than blocking output
- Fallback: template report when LLM fails or no evidence
- 14 tests in `tests/unit/test_report_node.py` — 164 unit tests total

---

## ~~Phase 9 — LLM prompt hardening~~ ✅ DONE (delivered with Phase 1)

Retry/backoff, `response_format: json_object`, JSON validation, and model fallback chain all shipped in `services/openrouter.py`. Token budget guard deferred to a future hardening pass if needed.

---

## Delivery summary

All 9 phases complete. 164 unit tests passing. Integration tests hit real OpenRouter + yfinance APIs (~50s).

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
