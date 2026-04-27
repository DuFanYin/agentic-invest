# agentic-invest

LangGraph-based investment research agent. Given a query, it runs a multi-node pipeline (research → fundamental analysis → market sentiment → scenario scoring → report verification) and returns a structured Markdown report.

## Running tests

Activate the virtualenv first:

```bash
source .venv/bin/activate
```

### Unit tests (fast, no network, LLMs mocked)

```bash
PYTHONPATH=. pytest tests/unit/ -q
```

All ~126 tests should pass in under 2 seconds. No API keys required.

### Integration tests (require real API keys)

```bash
PYTHONPATH=. pytest tests/integration/ -q
```

Integration tests call real external services (OpenRouter LLM, yfinance, Tavily). They require `OPENROUTER_API_KEY` and optionally `TAVILY_API_KEY` to be set. Expect them to take 30–120 seconds and to be flaky under rate limits.

## Running the server

```bash
source .venv/bin/activate
PYTHONPATH=. uvicorn src.server.main:app --reload
```

API available at `http://localhost:8000`. Frontend at `src/frontend/index.html` (open directly in browser).

## Architecture

| Node | Purpose |
|---|---|
| `parse_intent` | Classify query → `ResearchIntent` |
| `research` | Fetch financial data + web search → `Evidence[]` + `NormalizedData` |
| `fundamental_analysis` | LLM analysis of financials → `FundamentalAnalysis` |
| `market_sentiment` | LLM analysis of news/price → `MarketSentiment` |
| `gap_check` | Detect evidence conflicts, surface `agent_questions` |
| `scenario_scoring` | LLM generates 3–5 scenarios with probability weights → `Scenario[]` |
| `report_verification` | LLM writes Markdown report, runs validation |

All inter-node data flows through typed Pydantic models defined in `src/server/models/`.

## Key conventions

- Each agent node raises `RuntimeError("[node_name] ...")` on unrecoverable failure — caught by LangGraph and surfaced via SSE.
- LLM calls go through `OpenRouterClient.call_with_retry` (for JSON output) or `complete_text` (for Markdown). The client cycles through a free model chain with retries before raising.
- Scenarios must have 3–5 entries, probabilities summing to 1.0, and at least one magnitude tag (`bearish-1..3`, `neutral`, `bullish-1..3`).
- `agent_questions` uses a custom LangGraph reducer (`_accumulate_or_reset`) with a `_RESET` sentinel to clear the list between graph passes.
