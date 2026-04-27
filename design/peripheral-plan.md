# Peripheral System Specification

## 1) Document Positioning

This document defines the peripheral specification for the investment research system, including system boundaries, interface design, interaction model, quality constraints, testing requirements, and risk controls. It is a static specification and does not include milestones or delivery timelines.

## 2) System Boundary

### In Scope
- Input: natural-language investment research query
- Output: Markdown or JSON report that includes at least:
  - Company/business overview
  - Financial snapshot
  - Valuation perspective
  - Bull vs Bear viewpoints
  - Future scenario analysis (at least 3 scenarios)
  - Scenario probabilities (sum of all scenario `probability` values must equal 1)
  - Key risks
  - Final conclusion summary (not financial advice)
  - Source links
- Interaction model: FastAPI + React-in-HTML frontend (no build step)

### Out of Scope
- Real-time trade execution
- Portfolio management UI
- Personalized financial advice
- Complex quantitative backtesting engine

## 3) Technical Stack

- Backend: Python + FastAPI
- Frontend: React-in-HTML (`index.html` inline Babel + React/ReactDOM CDN)
- Agent orchestration: LangGraph `StateGraph` orchestrator
- LLM adapter: OpenRouter API client
- Data sources: public financial APIs + macro data + news/web sources
- Storage: SQLite (task/result cache)
- Report format: Markdown + JSON

## 4) API and UI Specification

### FastAPI Endpoints
- `POST /research`
  - request: `{ "query": "..." }`
  - note: only `query` is accepted by the API request model; planning derives fields such as ticker/time_horizon/risk_level into `intent`
  - response: typed `ResearchResponse` JSON with fields `report_markdown`, `report_json`, `intent`, `evidence`, `fundamental_analysis`, `macro_analysis`, `market_sentiment`, `scenarios`, `scenario_debate`, `agent_statuses`, `validation_result`, `llm_calls`
- `POST /research/stream`
  - request: `{ "query": "..." }`
  - response: `text/event-stream`
  - events:
    - `agent_status`: full agent-status list snapshots (`agent`, `lifecycle`, `phase`, `action`, `details`, timestamps, wait/progress/retry/error fields)
    - `llm_call`: real-time LLM call telemetry (`calling/success/retry/failed`)
    - `final`: final full report object (same shape as `POST /research`)
    - `error`: terminal error payload when stream execution fails
    - `done`: stream completion marker
- `GET /`
  - returns HTML page with React app mounted in `#root`

### UI Interaction Rules (React-in-HTML)
- Inputs:
  - `query` (single input)
  - `ticker`, `horizon`, `risk_level` are not shown in the frontend form
- Frontend uses native `fetch` to call `POST /research/stream` for real-time progress
- UI includes:
  - Global status (`Idle/Running/Complete/Error`)
  - Agent Status panel (derived from streamed `lifecycle/phase/action/waiting_on/progress_hint/last_update_at`)
  - Model-call log stream (event history + timestamps)
  - Final result area (Summary, Intent, Fundamental Analysis, Macro Analysis, Market Sentiment, Scenarios, Scenario Debate, Evidence, Validation badge)
- UI displays scenario probability sum (`∑ probability`) and relies on backend `validation_result` for `VALID/REVIEW` status
- UI handles stream interruptions explicitly: `done` without `final/error` is treated as interrupted execution

## 5) Quality and Evaluation Mapping

- Correctness: key conclusions must be evidence-backed and pass data consistency checks
- Scenario quality: scenarios are complete, probabilities are explainable, and `sum(probability) = 1`
- Modularity: clean responsibility separation, stable interfaces, clear boundaries
- Testability: tool layer, parsing layer, and validation layer are repeatably testable
- Code quality: typed models, explicit interfaces, isolated services

## 6) Testing and Validation Requirements

### Unit Tests
- Planning/query parser
- Financial metric calculator
- Retry gate routing
- Scenario debate calibration
- Schema validators
- Scenario normalization and `sum=1` validator

### Integration Tests
- Use mocked API responses to verify end-to-end flow
- Verify SSE contract (`agent_status`, `final`, `error`, `done`) and `/research` response contract
- Verify multi-scenario output shape (at least 3 scenarios + probability sum equals 1 in mocked flow)

### Regression Tests
- No dedicated golden-snapshot regression suite is currently implemented

## 7) Risk Control Principles

- API rate limits: caching + exponential backoff + fallback sources
- Hallucinated conclusions: strict citation validation before output
- Stale data: full timestamping and freshness display in reports
- High latency: parallel execution + context window control
- Probability subjectivity: explicitly disclose assumptions, triggers, and confidence intervals
