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
- Agent orchestration: LangGraph or equivalent custom orchestrator
- LLM adapter: OpenRouter API client
- Data sources: public financial APIs + news sources
- Storage: SQLite/Postgres (task/result cache)
- Report format: Markdown + JSON

## 4) API and UI Specification

### FastAPI Endpoints
- `POST /research`
  - request: `{ "query": "..." }`
  - note: frontend submits only `query`; `ticker`, `horizon`, and `risk_level` are inferred by the Orchestrator
  - response: typed `ResearchResponse` JSON (includes `report_markdown`, `report_json`, `intent`, `evidence`, `fundamental_analysis`, `market_sentiment`, `scenarios`, `agent_statuses`, `validation_result`, `llm_calls`)
- `POST /research/stream`
  - request: `{ "query": "..." }`
  - response: `text/event-stream`
  - events:
    - `agent_status`: each agent's `status/action/details`
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
  - Agent Status panel (each agent's current status and action)
  - Model-call log stream (event history + timestamps)
  - Final result area (Summary, Intent, Evidence, Fundamental Analysis, Market Sentiment, Scenarios, Validation)
- UI performs probability validation: if scenario `probability` sum is not 1, show error and mark result for review

## 5) Quality and Evaluation Mapping

- Correctness: key conclusions must be evidence-backed and pass data consistency checks
- Scenario quality: scenarios are complete, probabilities are explainable, and `sum(probability) = 1`
- Modularity: clean responsibility separation, stable interfaces, clear boundaries
- Testability: tool layer, parsing layer, and validation layer are repeatably testable
- Code quality: typed models, explicit interfaces, isolated services

## 6) Testing and Validation Requirements

### Unit Tests
- Query parser
- Financial metric calculator
- Schema validators
- Scenario normalization and `sum=1` validator

### Integration Tests
- Use mocked API responses to verify end-to-end flow
- Verify timeout and retry behavior
- Verify multi-scenario output shape (at least 3 scenarios + probability sum equals 1)

### Regression Tests
- Compare outputs of typical queries against golden snapshots

## 7) Risk Control Principles

- API rate limits: caching + exponential backoff + fallback sources
- Hallucinated conclusions: strict citation validation before output
- Stale data: full timestamping and freshness display in reports
- High latency: parallel execution + context window control
- Probability subjectivity: explicitly disclose assumptions, triggers, and confidence intervals
