# Agent Assignment

## Project Intro

This is an investment research tool that can take on any investment-related query and turn scattered information into a structured research report.

The motivation is simple: as someone who invests, I often get drowned in numbers, indicators, headlines, and market noise. Even after reading a lot, it can still be hard to understand what actually matters and how today’s signals might shape future outcomes.

What I really want to know is: is the company fundamentally doing well? What are the bullish and bearish voices in the market? What future scenarios could happen, and how likely is each one? Once I have that picture, I can make a more informed decision before I yolo my money.

The system is designed around that workflow. It searches for sources, builds a shared research state, runs fundamental and sentiment analysis, formulates future scenarios with likelihood scores, and finally produces a validated report. 


## Key Features

- Multi-agent feedback cycle (not a one-pass pipeline): the graph can re-enter research when open questions remain.
- Two-layer agent status model: `lifecycle` (`standby/active/waiting/blocked/failed`) + `phase` (task semantics such as `collecting_evidence`, `generating_report`).
- Real-time SSE streaming for UI observability (`agent_status`, `state_update`, `timeline`, `final`).


## Structure

This repository follows the layout defined in `design/codebase-structure.md`:

- `design/`: architecture and product design docs
- `src/server/`: FastAPI backend, routes, models, agents, services, utils
- `src/frontend/`: frontend page and static assets
- `tests/`: unit and integration tests
- `outputs/`: sample generated report outputs

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn src.server.main:app --reload
```

## Environment Variables

Copy `.env.example` to `.env` (or export variables in your shell) before running
LLM-based intent parsing:

```bash
cp .env.example .env
```


## TODO

Define explicit conditions for agent-generated questions that trigger another research round.
Potential improvements:
- Add dynamic thresholds for retry decisions.
- Attach confidence scores to intermediate outputs.
- Automatically retry or re-verify when confidence is below threshold.


fix: more robust json output format from model

strong typed results between agents

failaure qurentine

async flow? find a way to share research state so model work concurretnly


more ovservability