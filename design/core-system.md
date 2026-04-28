# Multi-Agent System Design

This document explains the runtime design of the multi-agent research system:
which agents exist, how they share state, when they run in parallel, and how
retry decisions are made.

For repository navigation, read [`codebase.md`](codebase.md). For model prompts
and expected LLM outputs, read [`llm-callpoints.md`](llm-callpoints.md).

## 1) Design Goal

The system turns a natural-language investment question into a validated research
report through a coordinated agent graph.

The graph is designed around five principles:

- Each agent writes structured state, not free-form handoff text.
- Evidence is accumulated across research passes instead of overwritten.
- Analysis work fans out in parallel after evidence collection.
- Retry routing is policy-driven, not decided directly by an LLM response.
- Final reports must remain traceable to evidence and pass validation checks.

## 2) Graph At A Glance

```text
parse_intent
  -> research
  -> [fundamental_analysis, macro_analysis, market_sentiment]
  -> llm_judge
  -> policy_router
       -> research          (retry)
       -> scenario_scoring  (continue)
  -> scenario_debate
  -> report_finalize
```

The only active loop is from `policy_router` back to `research`.

## 3) Runtime Topology

```text
START
  -> parse_intent
  -> research
  -> fundamental_analysis ──┐
  -> macro_analysis       ──┼─> llm_judge -> policy_router
  -> market_sentiment     ──┘                   │
                                                ├─ retry -> research
                                                └─ continue -> scenario_scoring
                                                                 -> scenario_debate
                                                                 -> report_finalize
                                                                 -> END
```

Important topology constraints:

- `fundamental_analysis`, `macro_analysis`, and `market_sentiment` run in parallel.
- `llm_judge` only produces a retry hint.
- `policy_router` makes the final routing decision.
- Scoped retry re-enters the same `research` node with `retry_scope` set.

## 4) Shared State Model

All agents communicate through `ResearchState`.

State is easier to understand in groups:

| Group | Fields | Purpose |
|---|---|---|
| Input / planning | `query`, `intent`, `plan_context` | User question and planning output |
| Evidence / data | `evidence`, `normalized_data` | Collected evidence and normalized metrics/conflicts |
| Analysis | `fundamental_analysis`, `macro_analysis`, `market_sentiment` | Parallel analysis outputs |
| Policy / retry | `policy_decision`, `retry_scope`, `retry_reason`, `retry_questions`, `research_iteration` | Retry control and loop state |
| Scenario / report | `scenarios`, `scenario_debate`, `narrative_sections`, `report_markdown`, `report_json` | Final reasoning and report artifacts |
| Validation / status | `validation_result`, `quality_metrics`, `stop_reason`, `agent_statuses` | Quality checks and UI/runtime status |

Reducer rules:

- `evidence` uses `operator.add`, so each research pass appends new evidence.
- `retry_questions` uses plain replacement, so only the current retry question is active.
- `agent_statuses` uses a custom merge reducer, so parallel nodes can update statuses safely.

## 5) Agent Responsibilities

| Agent | Purpose | Writes | Failure mode |
|---|---|---|---|
| `parse_intent` | Infer intent and planning context from the raw query | `intent`, `plan_context`, initial status | Fail-safe fallback |
| `research` | Collect and normalize finance, macro, and web evidence | `evidence`, `normalized_data`, `research_iteration` | Fail-fast if no usable evidence |
| `fundamental_analysis` | Analyze business quality, valuation, and fundamental risks | `fundamental_analysis` | Degraded fallback |
| `macro_analysis` | Analyze macro regime, rates, growth, and external risks | `macro_analysis` | Degraded fallback |
| `market_sentiment` | Analyze news, price action, sentiment, and narrative risk | `market_sentiment` | Degraded fallback |
| `llm_judge` | Decide whether there is a meaningful evidence/analysis gap | `policy_decision` hint | Best-effort continue |
| `policy_router` | Convert judge hint and state signals into routing action | final `policy_decision`, `retry_scope`, `retry_questions`, `retry_reason` | Fail-fast |
| `scenario_scoring` | Generate 3-5 baseline forward scenarios | `scenarios` | Fail-fast |
| `scenario_debate` | Recalibrate scenario probabilities through advocate/arbitrator debate | `scenario_debate` | Degraded fallback |
| `report_finalize` | Render sections, assemble report JSON/Markdown, run validation | report outputs, validation, quality metrics | Fail-fast on core preconditions |

## 6) Research Agent Design

`research` is a coordinator over the capability layer.

It calls:

- `cap.fetch_finance` for company profile, statements, price, and news evidence.
- `cap.fetch_macro` for macro indicators and market signals.
- `cap.fetch_web` for web evidence.
- `cap.normalize` for `NormalizedData`, missing fields, and conflict signals.

The web path is adaptive:

- `research` asks an LLM to generate 3-5 targeted search queries.
- `cap.fetch_web` can run multiple queries concurrently.
- Web results are deduplicated by URL.
- If query planning fails, `research` falls back to deterministic queries.

Scoped retry is implemented inside this same node. If `retry_scope` is set to
`["cap.fetch_web"]`, research only reruns the web capability.

## 7) Policy-Driven Retry

Retry has two layers:

1. `llm_judge` evaluates whether another pass may help.
2. `policy_router` applies deterministic rules and writes the final route.

This split keeps LLM judgment separate from graph control.

Policy actions:

- `continue`: proceed to scenario generation.
- `retry_full_research`: rerun all research capabilities.
- `retry_capability_only`: rerun only targeted capabilities.
- `halt_with_degraded_output`: stop retrying and continue toward finalization/degraded handling.

Rule precedence is deterministic:

```text
iteration limit
  -> structural issue
  -> all analyses degraded
  -> evidence conflict
  -> analysis robustness
  -> default continue
```

## 8) Failure Semantics

The system distinguishes recoverable degradation from hard failure.

Degraded fallback:

- `fundamental_analysis`
- `macro_analysis`
- `market_sentiment`
- `llm_judge`
- `scenario_debate`
- per-section narrative rendering in `report_finalize`

Fail-fast:

- `research` when no usable evidence is collected
- `policy_router` when routing itself breaks
- `scenario_scoring` when valid scenarios cannot be produced
- `report_finalize` when no evidence exists or all three analysis nodes degraded

## 9) Report Composition

The final report combines structured payloads and narrative sections.

- Standard report sections come from `plan_context.report_plan`.
- Query-specific sections come from `plan_context.custom_sections`.
- Structured sections are rendered from typed backend payloads.
- Narrative/custom sections are written by `report_finalize`.
- Validation and quality metrics are computed in Python.

Default sections:

- Executive Summary
- Fundamental Analysis
- Macro Environment
- Market Sentiment
- Future Scenarios
- Scenario Calibration
- Conclusion & What To Watch
