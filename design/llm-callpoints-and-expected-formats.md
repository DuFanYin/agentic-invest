# LLM Callpoints and Expected Return Formats

This document lists all active LLM callpoints in `src/server/agents/*`, the expected response shape at each point, and how each output is validated or post-processed.

## Overview

| Node | File | Call API | Expected Output |
|---|---|---|---|
| `parse_intent` | `src/server/agents/planning_agent.py` | `llm_client.complete(...)` | JSON object (Intent + planning schema) |
| `fundamental_analysis` | `src/server/agents/fundamental_analysis.py` | `llm.call_with_retry(...)` | JSON object (`FundamentalAnalysis`) |
| `macro_analysis` | `src/server/agents/macro_analysis.py` | `llm.call_with_retry(...)` | JSON object (`MacroAnalysis`) |
| `market_sentiment` | `src/server/agents/market_sentiment.py` | `llm.call_with_retry(...)` | JSON object (`MarketSentiment`) |
| `scenario_scoring` | `src/server/agents/scenario_scoring.py` | `llm.call_with_retry(...)` | JSON object (`{"scenarios":[...]}` preferred) |
| `scenario_debate` | `src/server/agents/scenario_debate.py` | `llm.call_with_retry(...)` | JSON object (`ScenarioDebate`-like payload) |
| `report_finalize` | `src/server/agents/report_finalize.py` | `llm.complete_text(...)` | Markdown report text |

---

## 1) `parse_intent` (Planning Agent)

- **Location**: `src/server/agents/planning_agent.py`
- **Call**: `llm_client.complete(prompt, system=_SYSTEM, node="parse_intent")`
- **Expected format**: JSON object
- **Expected keys**:
  - `intent` (enum-like string)
  - `subjects` (string array)
  - `scope` (`company|sector|theme|macro|event|mixed`)
  - `ticker` (string or `null`)
  - `time_horizon` (string or `null`)
  - `risk_level` (`low|medium|high|null`)
  - `required_outputs` (string array)
  - `research_focus` (string array)
  - `must_have_metrics` (string array)
  - `plan_notes` (string array)
- **Post-processing / validation**:
  1. Parse with `json.loads(raw)`.
  2. Build `ResearchIntent(...)`.
  3. If planning fields are empty, derive minimal defaults.
  4. On any failure, return safe fallback intent + fallback planning fields (no exception thrown).

---

## 2) `fundamental_analysis`

- **Location**: `src/server/agents/fundamental_analysis.py` (`fundamental_analysis_node`)
- **Call**: `llm.call_with_retry(prompt, system=_SYSTEM, node="fundamental_analysis")`
- **Expected format**: strict JSON object
- **Expected content**:
  - `claims[]` with `statement`, `confidence`, `evidence_ids`
  - `business_quality`
  - `financials`
  - `valuation`
  - `fundamental_risks[]`
  - `missing_fields[]`
- **Post-processing / validation**:
  1. Parse JSON.
  2. Attach `metrics` from normalized research data.
  3. Validate via `FundamentalAnalysis.model_validate(...)`.
  4. If invalid/unavailable, raise `RuntimeError("[fundamental_analysis] ...")`.

---

## 3) `macro_analysis`

- **Location**: `src/server/agents/macro_analysis.py` (`macro_analysis_node`)
- **Call**: `llm.call_with_retry(prompt, system=_SYSTEM, node="macro_analysis")`
- **Expected format**: strict JSON object
- **Expected content**:
  - `macro_view`
  - `rate_environment`, `growth_environment`
  - `macro_drivers[]`
  - `macro_risks[]`
  - `macro_signals[]`
  - `missing_fields[]`
- **Post-processing / validation**:
  1. Parse JSON.
  2. Validate via `MacroAnalysis.model_validate(...)`.
  3. If invalid/unavailable, raise `RuntimeError("[macro_analysis] ...")`.

---

## 4) `market_sentiment`

- **Location**: `src/server/agents/market_sentiment.py` (`market_sentiment_node`)
- **Call**: `llm.call_with_retry(prompt, system=_SYSTEM, node="market_sentiment")`
- **Expected format**: strict JSON object
- **Expected content**:
  - `claims[]`
  - `news_sentiment`
  - `price_action`
  - `market_narrative`
  - `sentiment_risks[]`
  - `missing_fields[]`
- **Post-processing / validation**:
  1. Parse JSON.
  2. Validate via `MarketSentiment.model_validate(...)`.
  3. If invalid/unavailable, raise `RuntimeError("[market_sentiment] ...")`.

---

## 5) `scenario_scoring`

- **Location**: `src/server/agents/scenario_scoring.py` (`scenario_scoring_node`)
- **Call**: `llm.call_with_retry(prompt, system=_SYSTEM, node="scenario_scoring")`
- **Expected format**: JSON object with top-level `scenarios` array (parser also tolerates direct array)
- **Expected structure**:
  - `scenarios` length must be `3..5`
  - each scenario includes:
    - `name`, `description`
    - `raw_probability`
    - `drivers[]`, `triggers[]`, `signals[]`
    - `evidence_ids[]`
    - optional `time_horizon`
    - `tags[]` (parser defaults to `["neutral"]` when missing/empty; prompt asks for magnitude tag)
- **Post-processing / validation**:
  1. Parse JSON and unwrap `data["scenarios"]` if present.
  2. Convert to `Scenario` objects with `_parse_llm_scenarios(...)`.
  3. Normalize probabilities with `_normalise(...)`.
  4. Sort by probability descending.
  5. If generation fails, raise `RuntimeError("[scenario_scoring] unable to generate scenarios from LLM output")`.

---

## 6) `scenario_debate`

- **Location**: `src/server/agents/scenario_debate.py` (`scenario_debate_node`)
- **Call**: `llm.call_with_retry(prompt, system=_SYSTEM, node="scenario_debate")`
- **Expected format**: JSON object
- **Expected content**:
  - `debate_summary`
  - `probability_adjustments[]`
  - `calibrated_scenarios[]`
  - `confidence`
  - `debate_flags[]`
- **Post-processing / validation**:
  1. Parse JSON.
  2. Enforce hard constraints in `_validate_and_fix(...)`:
     - per-scenario delta cap (`<= 0.15`)
     - calibrated scenario coverage must include all baseline scenarios
     - probability normalization if needed
  3. On parse/LLM failure or invalid coverage, fallback to baseline via `_fallback_debate(...)` (no exception thrown).

---

## 7) `report_finalize`

- **Location**: `src/server/agents/report_finalize.py` (`report_finalize_node`)
- **Call**: `llm.complete_text(prompt, system=_SYSTEM, node="report_finalize")`
- **Expected format**: Markdown text (non-JSON)
- **Expected content**:
  - fixed section sequence (Executive Summary -> Disclaimer)
  - evidence ID citations where relevant
  - scenario debate calibration summary section
  - required disclaimer text: `Not financial advice.`
- **Post-processing / validation**:
  1. Accept non-empty content with minimal length check (`> 100` chars).
  2. Always run Python validations:
     - scenario probability checks
     - evidence completeness
     - claim coverage
  3. Append `## Validation Errors` / `## Validation Warnings` to report markdown when present.
  4. Compute `quality_metrics` (citation coverage, probability validity, debate_applied, unresolved issues, confidence).
  5. If report generation fails, raise `RuntimeError("[report_finalize] ...")`.

---

## `OpenRouterClient` Common Behavior

- **File**: `src/server/services/openrouter.py`
- `complete(...)` and `call_with_retry(...)` run in JSON mode by default:
  - sends `response_format: {"type": "json_object"}`
  - strips fenced wrappers when needed
  - validates response JSON
  - treats invalid JSON and several transport/provider errors as retryable
- `complete_text(...)` runs with `json_mode=False`:
  - no JSON enforcement
  - used for final Markdown report generation

Retry / failover semantics:
- per-model retries with exponential backoff (`max_retries` controls attempts per model)
- retryable HTTP/network/timeout/invalid-JSON paths stay on current model until exhausted
- fatal errors skip directly to next model
- raises only after all models are exhausted

---

## Failure-Handling Notes

- `parse_intent` and `scenario_debate` are fallback-friendly:
  - `parse_intent`: returns default intent/planning on failure
  - `scenario_debate`: returns baseline probabilities with `fallback_to_baseline` flag
- analysis/scoring/report nodes are fail-fast:
  - `fundamental_analysis`, `macro_analysis`, `market_sentiment`, `scenario_scoring`, `report_finalize` raise `RuntimeError` if LLM output is unusable

---

## `llm_call` Streaming Telemetry

- **Related files**:
  - `src/server/services/collector.py`
  - `src/server/services/openrouter.py`
  - `src/server/agents/orchestrator.py`
- **Mechanism**:
  1. `OpenRouterClient` emits lifecycle events (`calling/success/retry/failed`) into `LLMCallCollector`.
  2. Collector stores full history and pushes real-time events to an `asyncio.Queue`.
  3. `OrchestratorAgent.run_stream(...)` interleaves graph progress with queue events.
- **Result**:
  - near real-time `llm_call` SSE updates during execution
  - final response still includes full `llm_calls` history for auditability
