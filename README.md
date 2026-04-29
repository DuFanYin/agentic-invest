# Agentic Invest

## Project Intro

Agentic Invest is a multi-agent investment research system that turns open-ended market questions into structured, evidence-backed reports.

Given a query, the system plans the research scope, gathers finance/macro/web evidence, runs parallel analysis nodes, evaluates whether additional evidence is needed, calibrates forward scenarios, and produces a validated report in Markdown and JSON formats.

The goal is to reduce noise from scattered metrics and headlines, and provide a clearer decision view: what the fundamentals say, what market sentiment implies, which scenarios are plausible, and what to watch next.

<img src="design/design.svg" alt="System Design Overview" width="900" />

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
PYTHONPATH=. uvicorn src.server.main:app --reload
```

Server starts on `http://localhost:8000`.

## Environment Variables

Environment configuration is defined in [`.env.example`](.env.example).

## Code Quality

```bash
ruff format .
ruff check . --fix
ruff check .
```

## Running tests

```bash
source .venv/bin/activate
PYTHONPATH=. pytest tests/unit/ -q
PYTHONPATH=. pytest tests/integration/ -q
```

Integration tests require `LLM_API_KEY` (see `.env.example`).

## Structure

- `src/server/`: FastAPI backend, orchestration graph, capability layer, services, models, and utilities.
- `src/frontend/`: static frontend used for local interactive runs.
- `design/`: architecture, callpoint, frontend, and test documentation.
- `tests/`: unit and integration tests.
- `outputs/`: generated artifacts (including local cache/report outputs).


## Key Design Considerations

- Runtime layering: `routes → agents → capabilities → services → utils/models` with a centralised agent registry as source of truth for node contracts and dependencies.
- All inter-node data flows through validated Pydantic models — schema drift is caught at parse time, not propagated silently downstream.
- State contract enforcement via `registry.py` + `contract.py` constrains per-node read/write fields, reducing hidden state coupling and regression risk.
- Node logic is testable in isolation because contracts are explicit and dependencies are injectable; policy retry rules are testable deterministically, independent of LLM variance.
- Iteration-aware judging: the LLM judge’s strictness scales down as `research_iteration` increases, while a hard max batch count and policy `iteration_limit` still cap supplemental research — tight quality checks without endless loops.
- Retry-aware analysis: analysis nodes include gate-oriented context on supplemental batches, so synthesis explicitly treats extra evidence as addressing the gate rather than repeating the same prompt with a larger dossier alone.
- LLM client implements model-chain failover, per-attempt telemetry, and interruptible backoff — resilient to transient provider failures.
- Best-effort degradation for non-critical LLM failures; hard fail only on unrecoverable preconditions.
- Evidence IDs use `iteration_id * 100 + offset` — simple collision prevention across retry passes without coordination overhead.
- `seen_urls` dedup across concurrent web fetches is safe under asyncio cooperative multitasking (no `await` between check and add).
- Live SSE streaming with `lifecycle` + `phase` status model gives the frontend fine-grained visibility into node progress.


## Design Docs

- [`design/codebase.md`](design/codebase.md): repository layout and runtime layering.
- [`design/core-system.md`](design/core-system.md): end-to-end graph topology, state contract, and node responsibilities.
- [`design/llm-callpoints.md`](design/llm-callpoints.md): active LLM callsites, expected formats, and failure behavior.
- [`design/frontend.md`](design/frontend.md): frontend interaction model and streaming UX.
- [`design/test-suite.md`](design/test-suite.md): test strategy and coverage map.