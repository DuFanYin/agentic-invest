# Core Agent System Design

## 1) System Goal

The goal is to build a multi-agent investment research system. Given any investment query, it should infer intent, collect evidence, run analysis, and output a traceable and verifiable report.

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
├── retry_questions[]        ← replaced each cycle (plain assign)
├── research_iteration       ← incremented by Research Agent; read by retry_gate
├── scenarios[]
├── scenario_debate
├── narrative_sections
├── report_markdown
├── report_json
├── validation_result
├── quality_metrics
└── agent_statuses[]         ← _last_list reducer (merges per-agent parallel writes)
```

Core principles:

- Every key conclusion must be traceable to evidence.
- Agents exchange structured objects instead of long free-form text.
- Agents do not run in a rigid sequence; they can work in parallel and request additional data when gaps are detected.
- Final output must pass validation, especially citation integrity, data completeness, and scenario probability checks (`sum(probability)=1`).
- Query types are not hardcoded. The Orchestrator dynamically infers intent, subject, time horizon, and required data.

## 2) Core Inputs and Outputs

Input:

- `query`: user natural-language question

Other fields are not required user inputs. They are inferred from `query` by the Orchestrator:

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

## 3) Core Data Objects

### Research Intent Object

The Orchestrator converts a natural-language query into a general research intent object.

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

### Evidence Object

Each evidence item follows a unified schema for consistent downstream references.

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

### Agent Output Object

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
  "metrics": {},
  "missing_fields": []
}
```

## 4) Collaboration Model and Topology

The system uses a LangGraph `StateGraph` with a shared `ResearchState`. The graph has one active retry loop after `retry_gate` (evidence adequacy/conflict checks).

Core flow:

- `parse_intent` (implemented by planning agent) extracts `ResearchIntent` and planning fields from raw query.
- `research` collects evidence from financial APIs, macro sources, yfinance news, and Tavily web search. Runs again on retry iterations.
- `fundamental_analysis`, `macro_analysis`, and `market_sentiment` run **in parallel** after each research iteration.
- `retry_gate` merges structural gaps (company scope without ticker) and research conflict signals (`normalized_data.conflicts`) to decide whether to retry.
- If gaps remain and retry budget exists, the graph loops back to `research` for a supplementary iteration.
- `scenario_scoring` runs once evidence gaps are resolved; probabilities are normalised in Python before constructing `Scenario` objects.
- `scenario_debate` calibrates scenario probabilities and can fallback to baseline probabilities when debate output is invalid.
- `report_finalize` generates Markdown via LLM, runs pure-Python validation, and computes quality metrics.
- The graph terminates after `report_finalize`; validation errors are surfaced in report output and `validation_result`.

```text
                              ┌─────────────────────────────────────────┐
                              │ retry: retry_questions detected,        │
                              │ research_iteration < 2                  │
                              ▼                                          │
START → parse_intent → research → fundamental_analysis ──┐              │
                                → macro_analysis       ───┼─→ retry_gate ┤
                                → market_sentiment    ───┘              │
                                                                         │
                                                          (no gaps) ─────┘
                                                               ↓
                                                      scenario_scoring
                                                               ↓
                                                      scenario_debate
                                                               ↓
                                                     report_finalize
                                                               END
```

State mutation rules:
- `evidence[]`: `operator.add` — each research iteration appends; never overwritten.
- `retry_questions[]`: plain replace — `retry_gate` (and later `report_finalize`) reset to current cycle list; accumulation would break retry termination.
- `agent_statuses[]`: `_last_list` custom reducer — parallel nodes can write this field in the same graph step; plain LastValue would raise `InvalidUpdateError`. The reducer merges per-agent updates and prefers newer `last_update_at` snapshots.

## 5) Agent Responsibilities

### Orchestrator Agent

- Parse user queries and produce an execution plan
- Infer intent, subjects, horizon, and expected outputs
- Execute the LangGraph workflow (fan-out/fan-in + conditional retry routing)
- Handle retry routing and failure surfacing
- Input: `query`
- Output: `Research State`, task plan, execution status, and final aggregated context
- Key strategies:
  - Support mixed intent without hardcoded query classes
  - Extract subjects automatically and select modules by intent
  - Run analysis nodes in parallel after each research pass
  - Route retries through `retry_gate` for evidence adequacy/conflict resolution

### Research Agent

- Retrieve public finance data (`finance_data`: company info, financials, price history, yfinance news) and web sources (`web_research`: Tavily)
- Retrieve macro data (`macro_data`: FRED + market signal proxies)
- Assign reliability levels to sources
- Deduplicate web results by URL within each pass
- Organize output as `evidence[]`
- Convert heterogeneous data into normalized `normalized_data`
- Input: research plan, subjects, keywords, and horizon
- Output: `evidence[]` + `normalized_data`
- Key strategies:
  - Prioritize high-quality sources (`financial_api` → `news` → `web`)
  - Evidence schema carries `summary`/`reliability`; collectors populate `retrieved_at`; `url` remains optional
  - Normalize fields and units; mark `missing_fields` when data is absent
  - Include conflict signals (`normalized_data.conflicts`) from cumulative evidence across iterations
  - If no usable evidence can be collected, raise a runtime error (no synthetic low-reliability fallback)

### Fundamental Analysis Agent

- Analyze business quality: model, competitive advantage, market position, growth drivers
- Analyze financial performance: revenue, margins, cash flow, leverage, capex
- Analyze valuation: multiples, historical ranges, comparable peers, optional simplified DCF
- Analyze fundamental risks: capital structure, regulation, supply chain, customer concentration, execution risks
- Separate facts, interpretation, and inference
- Input: `evidence[]` + `normalized_data`
- Output: `fundamental_analysis` (`business_quality`, `financials`, `valuation`, `fundamental_risks`)
- Key strategies:
  - Use normalized metrics context (TTM / three-year average / latest quarter when available)
  - Bind key judgments to `evidence_ids`
  - Write `missing_fields[]` when data is absent; surfaced in final report warnings
  - Cross-check valuation and attach observable risk signals

### Market Sentiment Agent

- Analyze news sentiment: recent news, disclosures, analyst views, market focus
- Analyze price action: short/mid-term trend, volatility, volume, relative strength
- Analyze market narrative: what story the market is pricing, whether expectations are overheated or cold
- Analyze sentiment risks: crowded trades, expectation resets, event-driven volatility
- Input: `evidence[]` + `normalized_data`
- Output: `market_sentiment` (`news_sentiment`, `price_action`, `market_narrative`, `sentiment_risks`)
- Key strategies:
  - Separate long-term fundamental change from short-term sentiment
  - Bind sentiment conclusions to market evidence
  - Downweight noisy signals
  - Write `missing_fields[]` when coverage is insufficient; surfaced as report warnings

### Scenario Scoring Agent

- Build forward-looking scenario states (3-5 distinct futures), with directional tags as metadata rather than primary buckets
- Assign probability `probability` (0-1) to each scenario
- Normalize probabilities before output to ensure total `probability` sum is 1
- Include key triggers for each scenario
- Input: `evidence[]` + `normalized_data` + `fundamental_analysis` + `market_sentiment`
- Output: `scenarios[]` (`name/description/probability/drivers/triggers/evidence_ids/tags`)
- Key strategies:
  - Keep at least 3 mutually exclusive scenarios
  - Normalize probabilities in Python to sum to 1 (post-LLM parsing)
  - Recompute when core assumptions change
  - State assumptions, triggers, and validation signals

### Macro Analysis Agent

- Analyze macro environment: growth/rates regime, key drivers, and macro risks
- Input: `evidence[]` (especially `macro_api`) + intent context
- Output: `macro_analysis` (`macro_view`, `macro_drivers`, `macro_risks`, regime tags)
- Key strategies:
  - Keep output concise and decision-relevant
  - Surface missing macro fields via `missing_fields[]` for report warnings

### Scenario Debate Agent

- Revisit scenario probabilities through structured bull/bear/judge calibration
- Input: baseline `scenarios[]` + fundamental/macro/sentiment context
- Output: `scenario_debate` (`debate_summary`, `probability_adjustments`, `calibrated_scenarios`, `debate_flags`)
- Key strategies:
  - Enforce bounded per-scenario shifts and full scenario coverage
  - Fallback to baseline probabilities on invalid debate output

### Report Finalize Agent

- Organize scenario implications and decision-relevant contrasts across the generated futures
- Generate the user-facing research report
- Check whether every key conclusion is evidence-backed
- Check scenario probabilities sum to 1
- Check for obvious internal contradictions
- Input: `evidence[]` + `normalized_data` + `fundamental_analysis` + `macro_analysis` + `market_sentiment` + `scenarios[]` + `scenario_debate`
- Output: `narrative_sections` + `report_markdown` + `report_json` + `validation_result` + `quality_metrics`
- Key strategies:
  - Require evidence references for key conclusions
  - Show "what we know / uncertainty / what to watch"
  - Validation always runs in pure Python (no LLM); errors/warnings appended inline
- Final node clears `retry_questions` and marks workflow completion

## 6) Final Report Structure

The final report follows a fixed structure for easier comparison, testing, and frontend rendering.

- Executive Summary
- Company Overview
- Key Evidence
- Fundamental Analysis
- Macro Environment
- Market Sentiment
- Valuation View
- Risk Analysis
- Future Scenarios
- Scenario Debate & Calibration
- Scenario Implications
- What To Watch Next
- Sources
- Disclaimer: Not financial advice.
