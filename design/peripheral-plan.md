# Peripheral System Overview

## 1) System Role

The system is an investment research application that accepts a natural-language query, runs a multi-stage research and analysis workflow, and returns a traceable report in Markdown and JSON forms.

## 2) System Boundary

### In Scope

- Natural-language investment research queries
- Backend research orchestration, evidence collection, analysis, scenario generation, and report assembly
- Structured and narrative report output with evidence references
- Browser-based frontend for query submission, progress visibility, and final result display
- Streaming progress and final-result delivery over HTTP

### Out of Scope

- Real-time trade execution
- Portfolio management workflows
- Personalized financial advice
- Backtesting or portfolio simulation engines

## 3) External Surface

- `POST /research`: synchronous research request returning structured final output
- `POST /research/stream`: streaming research request returning progress events and final output
- `GET /health`: readiness check (LLM key/provider status)
- `GET /`: frontend entry page

## 4) User-Facing Interaction Model

- The user submits one free-form research query
- The system runs backend planning, evidence gathering, analysis, scenario, and report stages
- The frontend displays execution status, model-call activity, section updates, and final report content
- The final result is presented as both machine-readable structured data and user-readable report content

## 5) System Shape

- Backend: Python + FastAPI
- Frontend: React-in-HTML without a build step
- Orchestration: LangGraph shared-state workflow
- LLM access: provider-agnostic OpenAI-compatible client (`openrouter` or `openai`)
- Data inputs: public finance, macro, news, and web sources
- Local infrastructure: SQLite-backed cache and request-scoped streaming queues

## 6) Output Characteristics

- Report output includes evidence-backed analysis, scenario views, risks, and final synthesis
- Scenario probabilities are normalized so total `probability` equals 1
- Output is available as Markdown for reading and JSON for rendering and downstream checks
- Execution may also emit streamed status, telemetry, section, error, and completion events

## 7) System Constraints

- Key conclusions are expected to be grounded in collected evidence
- Validation is part of the normal output path
- The system prioritizes modular service boundaries and typed data exchange
- Long-running execution is surfaced incrementally rather than only at completion
