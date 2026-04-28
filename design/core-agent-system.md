# Core Agent System Design

## 1) System Goal

The goal is to build a multi-agent investment research system that infers intent, collects evidence, runs analysis, and outputs a traceable, verifiable report.

The system is not a fixed linear pipeline. The Orchestrator runs a shared-state graph with parallel analysis stages and conditional retry routing based on gaps and progress.

```text
Shared Research State
├── query
├── intent
├── plan_context             ← planning output bundle (focus/metrics/notes/report plan/custom sections)
├── evidence[]               ← appended across iterations (operator.add)
├── normalized_data
├── fundamental_analysis
├── macro_analysis
├── market_sentiment
├── policy_decision          ← llm_judge hint, finalized by policy_router
├── retry_scope              ← capability scope for scoped retry (e.g. ["cap.fetch_web"])
├── retry_reason             ← current retry cause (`structural|analysis_robustness|evidence_conflict|judge_degraded|none`)
├── retry_questions[]        ← replaced each cycle (plain assign)
├── research_iteration       ← incremented by Research Agent; read by llm_judge
├── scenarios[]
├── scenario_debate
├── narrative_sections
├── report_markdown
├── report_json
├── validation_result
├── quality_metrics
├── stop_reason
└── agent_statuses[]         ← _last_list reducer (merges per-agent parallel writes)
```

Core principles:

- Every key conclusion must be traceable to evidence.
- Agents exchange structured objects instead of long free-form text.
- Agents do not run in a rigid sequence; they can run in parallel and request additional data when gaps are detected.
- Final output must pass validation, especially citation integrity, data completeness, and scenario probability checks (`sum(probability)=1`).
- Query types are not hardcoded. The planning stage dynamically infers intent, subject, time horizon, and required data from the raw query.

## 2) Core Inputs and Outputs

Input:

- `query`: user natural-language question

Other fields are not required user inputs. They are inferred from `query` by the planning stage:

- `ticker`: stock ticker; nullable if not identifiable
- `time_horizon`: investment horizon, e.g. `6 months`, `3 years`, `5 years`
- `risk_level`: risk preference, e.g. `low`, `medium`, `high`

Output:

- `report_markdown`: human-readable report
- `report_json`: structured output for frontend rendering and testing
- `intent`: inferred research intent and scope
- `evidence[]`: core evidence used by the system
- `fundamental_analysis`: business quality, financial performance, valuation, and fundamental risk analysis
- `macro_analysis`: macro regime, drivers, risks, and signals
- `market_sentiment`: news, market narrative, price action, and investor sentiment analysis
- `scenarios[]`: forward-looking scenarios; all `probability` values must sum to 1
- `scenario_debate`: post-scoring probability calibration and adjustment rationale
- `validation_result`: final validation summary, including missing fields, citation coverage, and probability checks
- `report_json.quality_metrics`: final quality snapshot (citation coverage, probability validity, debate applied, unresolved issues, confidence)

## 3) Core Data

### Research Intent

The planning stage converts a natural-language query into a general research intent object.

```json
{
  "intent": "investment_research|comparison|scenario_analysis|risk_review|valuation_check|market_event_analysis",
  "subjects": ["NVDA", "AI semiconductor supply chain"],
  "scope": "company|sector|theme|macro|event|mixed",
  "time_horizon": "5 years",
  "required_outputs": ["valuation", "risks", "scenarios"],
  "constraints": ["not financial advice"]
}
```

### Evidence

Each evidence item follows a unified schema for downstream references.

```json
{
  "id": "ev_001",
  "source_type": "filing|financial_api|news|web|company_site",
  "title": "...",
  "url": "...",            ← optional (nullable)
  "published_at": "...",  ← optional
  "retrieved_at": "...",  ← optional (present on collected evidence)
  "summary": "...",       ← required
  "reliability": "high|medium|low",  ← required
  "related_topics": ["revenue", "margin", "risk"]
}
```

### Representative Analysis Output

Each analysis agent outputs a structured result with explicit evidence bindings.

```json
{
  "agent": "fundamental_analysis",
  "claims": [
    {
      "statement": "...",
      "confidence": "high|medium|low",
      "evidence_ids": ["ev_001", "ev_003"]
    }
  ],
  "business_quality": { "view": "strong|stable|weak|deteriorating" },
  "valuation": { "relative_multiple_view": "..." },
  "fundamental_risks": [],
  "metrics": {},
  "missing_fields": []
}
```

## 4) Runtime Topology

The system uses a LangGraph `StateGraph` with a shared `ResearchState`. It has one active retry loop after `policy_router`.

Flow summary:

- `parse_intent` extracts planning context from the raw query.
- `research` collects evidence and may run again on retry iterations.
- `fundamental_analysis`, `macro_analysis`, and `market_sentiment` run in parallel after each research pass.
- `llm_judge` writes a retry hint (`policy_decision`).
- `policy_router` applies deterministic policy rules and routes retry or continue.
- `scenario_scoring` generates baseline scenarios, then `scenario_debate` recalibrates them.
- `report_finalize` renders narrative sections, assembles the export report, and runs final validation.

```text
START → parse_intent → research → fundamental_analysis ──┐
                                → macro_analysis       ───┼─→ llm_judge → policy_router
                                → market_sentiment    ───┘                  │
                                                                            ├─ (retry) → research
                                                                            └─ (continue) → scenario_scoring
                                                                                           ↓
                                                                                  scenario_debate
                                                                                           ↓
                                                                                 report_finalize
                                                                                           END
```

State mutation rules:
- `evidence[]`: `operator.add` — each research iteration appends; never overwritten.
- `retry_questions[]`: plain replace — `llm_judge` and `report_finalize` overwrite this cycle's list; accumulation would break retry termination.
- `agent_statuses[]`: `_last_list` custom reducer — parallel nodes can write this field in the same graph step; plain LastValue would raise `InvalidUpdateError`. The reducer merges per-agent updates and prefers newer `last_update_at` snapshots.

## 5) Runtime Layers

### Orchestration Layer

The orchestrator is a workflow/runtime coordinator, not a peer analysis agent.

- Build and execute the LangGraph workflow
- Instantiate and wire per-request runtime dependencies (`LLMClient`, `LLMCallCollector`, `SectionQueue`)
- Handle retry routing, streaming, and failure surfacing
- Input: `query`
- Output: graph execution, streamed runtime events, and final aggregated response
- Key strategies:
  - Run analysis nodes in parallel after each research pass
  - Route retries through `llm_judge` + `policy_router` for evidence adequacy/conflict resolution
  - Interleave graph updates, LLM telemetry, and section-ready events during streaming

### Node Roles

#### Planning Agent

- Role: parse the raw query into structured intent and planning context, and define the canonical report plan plus any query-specific custom sections
- Input: `query`
- Output: `intent` + `plan_context`
- Key strategies:
  - Support mixed intent without hardcoded query classes
  - Infer subjects, scope, ticker, horizon, and expected outputs from the query
  - Generate `research_focus`, `must_have_metrics`, and `plan_notes` for downstream use
  - Fall back to a safe default interpretation if planning output is unusable

#### Research Agent

- Role: collect finance, macro, and web inputs, then normalize them into reusable evidence and structured data
- Input: research plan, subjects, keywords, and horizon
- Output: `evidence[]` + `normalized_data`
- Key strategies:
  - Prioritize high-quality sources (`financial_api` → `news` → `web`)
  - Retrieve finance data, macro data, and Tavily web results in a common evidence schema
  - Evidence schema carries `summary`/`reliability`; collectors populate `retrieved_at`; `url` remains optional
  - Use adaptive web query planning: generate 3-5 targeted web queries from `research_focus`/`must_have_metrics` and retry question
  - Fall back to deterministic queries if the research query-planning LLM call fails
  - Deduplicate web results by URL within each pass
  - Support scoped retries via `retry_scope` (e.g. web-only retry)
  - Normalize fields and units; mark `missing_fields` when data is absent
  - Include conflict signals (`normalized_data.conflicts`) from cumulative evidence across iterations
  - If no usable evidence can be collected, raise a runtime error (no synthetic low-reliability fallback)

#### Fundamental Analysis Agent

- Role: evaluate business quality, financial performance, valuation, and core fundamental risks from the collected evidence
- Input: `evidence[]` + `normalized_data`
- Output: `fundamental_analysis` (`claims`, `business_quality`, `valuation`, `fundamental_risks`, `missing_fields`, `metrics`)
- Key strategies:
  - Use normalized metrics context (TTM / three-year average / latest quarter when available)
  - Bind key judgments to `evidence_ids`
  - Write `missing_fields[]` when data is absent; surfaced in final report warnings
  - Cross-check valuation and attach observable risk signals

#### Market Sentiment Agent

- Role: summarize market-facing signals across news, price action, positioning, and expectation risk
- Input: `evidence[]` + `normalized_data`
- Output: `market_sentiment` (`news_sentiment`, `price_action`, `market_narrative`, `sentiment_risks`)
- Key strategies:
  - Separate long-term fundamental change from short-term sentiment
  - Bind sentiment conclusions to market evidence
  - Downweight noisy signals
  - Write `missing_fields[]` when coverage is insufficient; surfaced as report warnings

#### Scenario Scoring Agent

- Role: construct the baseline forward scenarios, assign probabilities, and define the triggers that separate them
- Input: `evidence[]` + `fundamental_analysis` + `macro_analysis` + `market_sentiment` + planning context
- Output: `scenarios[]` (`name/description/probability/drivers/triggers/evidence_ids/tags`)
- Key strategies:
  - Keep at least 3 mutually exclusive scenarios
  - Normalize probabilities in Python to sum to 1 (post-LLM parsing)
  - Recompute when core assumptions change
  - State assumptions, triggers, and validation signals

#### Macro Analysis Agent

- Role: summarize the macro regime, its decision-relevant drivers, and the main external risks
- Input: `evidence[]` (especially `macro_api`) + intent context
- Output: `macro_analysis` (`macro_view`, `macro_drivers`, `macro_risks`, `rate_environment`, `growth_environment`, `missing_fields`)
- Key strategies:
  - Keep output concise and decision-relevant
  - Surface missing macro fields via `missing_fields[]` for report warnings

#### LLM Judge Agent

- Role: decide whether the pipeline should perform one more research pass before scenario generation
- Input: analysis outputs + evidence + planning context + conflict signals
- Output: `policy_decision` (hint action + reason + optional retry question)
- Key strategies:
  - Run a two-stage check: analysis robustness first, then conflict severity
  - Use a structural shortcut for missing company ticker
  - Bias toward proceeding unless the evidence gap or conflict is material
  - Treat judge failure as best-effort (`policy_decision.reason_code=judge_degraded`) and continue downstream

#### Policy Router Agent

- Role: finalize retry decision and route graph execution based on deterministic policy rules
- Input: `policy_decision` hint + state signals (iteration, degradation flags, conflict/missing counts)
- Output: finalized `policy_decision`, `retry_questions`, `retry_reason`, `retry_scope`
- Key strategies:
  - Apply explicit rule precedence (iteration limit > structural > all-degraded > conflict > robustness > default continue)
  - Route `retry_capability_only` to `research` with scoped capabilities
  - Preserve backward-compatible retry fields for downstream report warnings

#### Scenario Debate Agent

- Role: recalibrate baseline scenario probabilities through structured advocate/arbitrator debate
- Input: baseline `scenarios[]` + fundamental/macro/sentiment context
- Output: `scenario_debate` (`debate_summary`, `probability_adjustments`, `calibrated_scenarios`, `debate_flags`)
- Key strategies:
  - Enforce bounded per-scenario shifts and full scenario coverage
  - Fallback to baseline probabilities on invalid debate output

#### Report Finalize Agent

- Role: assemble the user-facing report, organize scenario implications, and run final validation
- Input: `evidence[]` + `fundamental_analysis` + `macro_analysis` + `market_sentiment` + `scenarios[]` + `scenario_debate` + planning context
- Output: `narrative_sections` + `report_markdown` + `report_json` + `validation_result` + `quality_metrics` (+ cleared `retry_questions`)
- Key strategies:
  - Require evidence references for key conclusions
  - Show "what we know / uncertainty / what to watch"
  - Validation always runs in pure Python (no LLM contradiction judge); errors/warnings appended inline
  - Fail fast when core report preconditions are not met (e.g. no evidence, or all three analysis nodes degraded)
  - Clear `retry_questions` and mark workflow completion at the final node

## 6) Report Composition

The final report is section-based rather than fully fixed. The planning stage provides a canonical `report_plan`, and `report_finalize` can append query-specific `custom_sections`. If planning is unavailable, the backend falls back to a small default section set.

### Section Sources

- Standard sections come from `report_plan.sections`
- Query-specific additions come from `custom_sections`
- Structured sections are rendered from typed backend payloads
- Narrative and custom sections are written by `report_finalize`

### Default Fallback Sections

Typical fallback sections are:

- Executive Summary
- Fundamental Analysis
- Macro Environment
- Market Sentiment
- Future Scenarios
- Scenario Calibration
- Conclusion & What To Watch
