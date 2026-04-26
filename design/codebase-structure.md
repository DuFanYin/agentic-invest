# Codebase Structure Design

## 1) Goal

The repository is organized by backend services, frontend pages, core agent system, shared models, tests, and docs. The goal is to keep the MVP simple while preserving room for future expansion.

## 2) Folder Structure

```text
agent-assignment/
├── design/
│   ├── core-agent-system.md
│   ├── peripheral-plan.md
│   ├── frontend.md
│   └── codebase-structure.md
├── src/
│   ├── server/
│   │   ├── main.py
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── health.py
│   │   │   └── research.py
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── request.py
│   │   │   ├── response.py
│   │   │   ├── evidence.py
│   │   │   ├── intent.py
│   │   │   └── scenario.py
│   │   ├── agents/
│   │   │   ├── __init__.py
│   │   │   ├── orchestrator.py
│   │   │   ├── research.py
│   │   │   ├── fundamental_analysis.py
│   │   │   ├── market_sentiment.py
│   │   │   ├── scenario_scoring.py
│   │   │   └── report_verification.py
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── openrouter.py
│   │   │   ├── finance_data.py
│   │   │   ├── web_research.py
│   │   │   └── cache.py
│   │   └── utils/
│   │       ├── __init__.py
│   │       ├── validation.py
│   │       ├── logging.py
│   │       └── formatting.py
│   └── frontend/
│       ├── index.html
│       └── static/
│           ├── app.js
│           └── styles.css
├── tests/
│   ├── unit/
│   │   ├── test_intent.py
│   │   ├── test_scenario_scoring.py
│   │   └── test_validation.py
│   └── integration/
│       └── test_research_api.py
├── outputs/
│   └── sample-report.md
├── README.md
└── problem-statment.md
```

## 3) Directory Responsibilities

### `design/`

Stores design documentation and does not participate in runtime execution.

- `core-agent-system.md`: core multi-agent system design
- `peripheral-plan.md`: peripheral system, API, testing, and risk-control spec
- `frontend.md`: Vanilla HTML layout design
- `codebase-structure.md`: repository structure specification

### `src/server/`

FastAPI backend entry and business logic.

- `main.py`: creates FastAPI app, registers routes, exposes `/`, `/health`, `/research`
- `routes/`: HTTP routes by feature
- `models/`: Pydantic request/response schemas and core data objects
- `agents/`: core agent implementations
- `services/`: wrappers for external APIs, LLM, data sources, cache, etc.
- `utils/`: shared helpers (logging, validation, formatting)

### `src/server/routes/`

The HTTP layer only receives requests, invokes business flow, and returns responses.

- `__init__.py`: exports route modules
- `health.py`: defines `/health`
- `research.py`: defines `/research` and calls the Orchestrator

### `src/server/models/`

Centralized Pydantic models to avoid schema scattering in business code.

- `__init__.py`: exports common schemas
- `request.py`: `ResearchRequest`
- `response.py`: `ResearchResponse`, `ValidationResult`
- `intent.py`: `ResearchIntent`
- `evidence.py`: `Evidence`
- `scenario.py`: `Scenario`

### `src/server/agents/`

Agents are split according to core architecture responsibilities.

- `orchestrator.py`: parses query, builds task plan, schedules other agents
- `research.py`: retrieves materials, organizes evidence, generates normalized data
- `fundamental_analysis.py`: runs fundamental, valuation, and fundamental-risk analysis
- `market_sentiment.py`: analyzes news, price action, market narrative, and sentiment risk
- `scenario_scoring.py`: generates future scenarios and guarantees `sum(score)=1`
- `report_verification.py`: generates report and performs final validation

### `src/server/services/`

Encapsulates external dependencies so agents do not call third-party APIs directly.

- `__init__.py`: exports service clients
- `openrouter.py`: OpenRouter adapter with model calls, error handling, and retries
- `finance_data.py`: financial data API wrapper
- `web_research.py`: web/news retrieval wrapper
- `cache.py`: cache read/write layer

### `src/server/utils/`

Contains stateless utility functions.

- `__init__.py`: exports shared utilities
- `validation.py`: scenario score, field completeness, and citation integrity checks
- `logging.py`: logging configuration
- `formatting.py`: Markdown/JSON formatting helpers

### `src/frontend/`

Vanilla HTML frontend without a heavy frontend framework.

- `index.html`: page structure and layout
- `static/app.js`: submit form, call `/research`, render result
- `static/styles.css`: optional stylesheet; can stay minimal if Tailwind CDN is used

### `tests/`

Testing directory.

- `unit/`: tests schemas, validators, scenario normalization, and small agent helpers
- `integration/`: tests `/research` end-to-end flow with mocked data sources

Suggested files:

- `unit/test_intent.py`: test query -> intent parsing
- `unit/test_scenario_scoring.py`: test score normalization and `sum(score)=1`
- `unit/test_validation.py`: test citation/field/probability validation
- `integration/test_research_api.py`: test basic `/research` API flow

### `outputs/`

Stores locally generated sample reports for demos and README references.

- `sample-report.md`: default sample output for README/demo

## 4) Current MVP Priorities

Keep the following files runnable first:

- `src/server/main.py`
- `src/frontend/index.html`
- `src/frontend/static/app.js`

Then incrementally expand:

- `models/`
- `agents/`
- `services/`
- `tests/`
