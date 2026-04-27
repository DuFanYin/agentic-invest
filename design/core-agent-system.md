# Core Agent System Design

## 1) System Goal

The goal is to build a multi-agent investment research system. Given any investment query, it should infer intent, collect evidence, run analysis, and output a traceable and verifiable report.

The system is not a fixed linear pipeline. The Orchestrator maintains a shared state and dynamically schedules agents based on gaps and progress.

```text
Shared Research State
├── query
├── intent
├── evidence[]               ← appended across passes (operator.add)
├── normalized_data
├── fundamental_analysis
├── market_sentiment
├── agent_questions[]        ← appended from parallel analysis nodes (operator.add)
├── scenarios[]
├── open_questions[]         ← replaced each cycle (plain assign, not accumulated)
├── research_pass            ← incremented by Research Agent; read by gap_check
├── report_markdown
├── report_json
├── validation_result
└── agent_statuses[]         ← _last_list reducer (handles parallel writes)
```

Core principles:

- Every key conclusion must be traceable to evidence.
- Agents exchange structured objects instead of long free-form text.
- Agents do not run in a rigid sequence; they can work in parallel and request additional data when gaps are detected.
- Final output must pass validation, especially citation integrity, data completeness, and scenario probability checks (`sum(score)=1`).
- Query types are not hardcoded. The Orchestrator dynamically infers intent, subject, time horizon, and required data.

## 2) Core Inputs and Outputs

Input:

- `query`: user natural-language question

Other fields are not required user inputs. They are inferred from `query` by the Orchestrator:

- `ticker`: stock ticker; nullable if not identifiable
- `horizon`: investment horizon, e.g. `6 months`, `3 years`, `5 years`
- `risk_level`: risk preference, e.g. `low`, `medium`, `high`

Output:

- `report_markdown`: human-readable report
- `report_json`: structured output for frontend rendering and testing
- `intent`: inferred research intent and scope
- `evidence[]`: core evidence used by the system
- `fundamental_analysis`: business quality, financial performance, valuation, and fundamental risk analysis
- `market_sentiment`: news, market narrative, price action, and investor sentiment analysis
- `scenarios[]`: forward-looking scenarios; all `score` values must sum to 1
- `validation_result`: final validation summary, including missing fields, citation coverage, and probability checks

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
  "retrieved_at": "...",  ← required
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

The system uses a LangGraph `StateGraph` with a shared `ResearchState`. The graph has two conditional retry checks: one after `gap_check`, and one after `report_verification` (for citation integrity gaps).

Core flow:

- `parse_intent` extracts a `ResearchIntent` from the raw query (ticker, scope, horizon, required outputs).
- `research` collects evidence from financial APIs, yfinance news, and Tavily web search. Runs again on retry passes.
- `fundamental_analysis` and `market_sentiment` run **in parallel** after each research pass.
- `gap_check` merges structural gaps (e.g. missing ticker/horizon), analysis-node `agent_questions`, and research conflict signals (`normalized_data.conflicts`) to decide whether to retry.
- If gaps remain and `research_pass < 2`, the graph loops back to `research` for a supplementary pass.
- `scenario_scoring` runs once gaps are resolved; scores are normalised in Python before constructing Scenario objects.
- `report_verification` generates the Markdown report via LLM, then runs pure-Python validation. Validation errors are appended as `## Validation Warnings`.
- If validation detects unsupported/missing evidence references and retry budget remains (`research_pass < 2`), the graph re-routes to `research`; otherwise it terminates.

```text
                              ┌─────────────────────────────────────────┐
                              │ retry: open_questions detected,         │
                              │ research_pass < 2                       │
                              ▼                                          │
START → parse_intent → research → fundamental_analysis ──┐              │
                                → market_sentiment    ───┴─→ gap_check ─┤
                                                                         │
                                                          (no gaps) ─────┘
                                                               ↓
                                                      scenario_scoring
                                                               ↓
                                                    report_verification
                                                               │
               ┌───────────────────────────────────────────────┴──────────────────────────────┐
               │ retry: unsupported/missing evidence claims detected, research_pass < 2       │
               └───────────────────────────────────────────────┬──────────────────────────────┘
                                                               ▼
                                                            research
                                                               │
                                                           (otherwise)
                                                               ▼
                                                               END
```

State mutation rules:
- `evidence[]`: `operator.add` — each research pass appends; never overwritten.
- `agent_questions[]`: `operator.add` — parallel analysis nodes append missing-field questions; `gap_check` drains and resets for the next cycle.
- `open_questions[]`: plain replace — `gap_check` resets to the current cycle's list; accumulation would break the termination check.
- `agent_statuses[]`: `_last_list` custom reducer — both parallel analysis nodes write this field in the same graph step; plain LastValue would raise `InvalidUpdateError`.

## 5) Agent Responsibilities

### Orchestrator Agent

- Parse user queries and produce an execution plan
- Dynamically infer intent, subjects, horizon, and expected outputs
- Dispatch tasks to specialized agents
- Handle retries, timeouts, and failure states
- Input: `query`
- Output: `Research State`, task plan, execution status, and final aggregated context
- Key strategies:
  - Support mixed intent without hardcoded query classes
  - Extract subjects automatically and select modules by intent
  - Run independent tasks in parallel
  - Retry failures and route all outputs through final verification

### Research Agent

- Retrieve public finance data (`finance_data`: company info, financials, price history, yfinance news) and web sources (`web_research`: Tavily)
- Assign reliability levels to sources
- Remove duplicate sources and repeated facts
- Organize output as `evidence[]`
- Convert heterogeneous data into normalized `normalized_data`
- Input: research plan, subjects, keywords, and horizon
- Output: `evidence[]` + `normalized_data`
- Key strategies:
  - Prioritize high-quality sources (`financial_api` → `news` → `web`)
  - Required evidence fields: `retrieved_at`, `summary`, `reliability`; `url` is optional
  - Normalize fields and units; mark `missing_fields` when data is absent
  - Deduplicate by URL; fall back to a single low-reliability item if all sources fail

### Fundamental Analysis Agent

- Analyze business quality: model, competitive advantage, market position, growth drivers
- Analyze financial performance: revenue, margins, cash flow, leverage, capex
- Analyze valuation: multiples, historical ranges, comparable peers, optional simplified DCF
- Analyze fundamental risks: capital structure, regulation, supply chain, customer concentration, execution risks
- Separate facts, interpretation, and inference
- Input: `evidence[]` + `normalized_data`
- Output: `fundamental_analysis` (`business_quality`, `financials`, `valuation`, `fundamental_risks`)
- Key strategies:
  - Cover at least 3 time slices (TTM / latest 3 years / latest quarter)
  - Bind key judgments to `evidence_ids`
  - Write `missing_fields[]` when data is absent; `gap_check` reads this to decide retries
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
  - Write `missing_fields[]` when coverage is insufficient; `gap_check` reads this to decide retries

### Scenario Scoring Agent

- Build forward-looking scenarios (e.g. bull/base/bear, or sector-driver scenarios)
- Assign probability `score` (0-1) to each scenario
- Normalize scores before output to ensure total `score` sum is 1
- Include key triggers and validation signals for each scenario
- Input: `evidence[]` + `normalized_data` + `fundamental_analysis` + `market_sentiment`
- Output: `scenarios[]` (`name/description/score/triggers/signals/evidence_ids`)
- Key strategies:
  - Keep at least 3 mutually exclusive scenarios
  - Enforce `abs(sum(scores) - 1) < 1e-6`
  - Recompute when core assumptions change
  - State assumptions, triggers, and validation signals

### Report & Verification Agent

- Organize bull/neutral/bear narratives
- Generate the user-facing research report
- Check whether every key conclusion is evidence-backed
- Check scenario probabilities sum to 1
- Check for obvious internal contradictions
- Input: `evidence[]` + `normalized_data` + `fundamental_analysis` + `market_sentiment` + `scenarios[]`
- Output: `report_markdown` + `report_json` + `validation_result`
- Key strategies:
  - Require evidence references for key conclusions
  - Show "what we know / uncertainty / what to watch"
  - Validation always runs in pure Python (no LLM); errors appended inline as `## Validation Warnings`
- Unsupported/missing-evidence claim errors are surfaced as `open_questions`; the graph may re-route to `research` when retry budget remains

## 6) Final Report Structure

The final report follows a fixed structure for easier comparison, testing, and frontend rendering.

- Executive Summary
- Company / Theme Overview
- Key Evidence
- Fundamental Analysis
- Market Sentiment
- Valuation View
- Risk Analysis
- Future Scenarios
- Bull / Base / Bear Thesis
- What To Watch Next
- Sources
- Disclaimer: Not financial advice.
