# Frontend Layout (Source of Truth: `src/frontend/index.html`)

## 1) Overview

This frontend design is a single-page React prototype mounted into `#root`, with a dense two-pane workstation layout:

1. Top command bar (`top-bar`)
2. Main workspace (`main-area`) split into left operations panel and right report panel
3. Runtime report footer (shown during running/complete/error states)

The interface is intentionally operator-style: compact typography, status-driven UI, and progressive reveal of report sections.

---

## 2) Visual Variants

The design supports two visual themes via `body[data-variant]`:

- `console` (default): terminal-like, JetBrains Mono dominant, CRT-like scanline overlay on report pane
- `dispatch`: editorial style, DM Serif + DM Sans mixed typography

Theme is tokenized with CSS variables (`--bg`, `--surface`, `--accent`, `--agent-*`, fonts, radius), so all components inherit a consistent palette and type system.

---

## 3) Layout Structure

### Top Command Bar (`top-bar`)

Single horizontal control strip:

- Left: wordmark (`Investment Research`)
- Center: query input + run button
  - `top-query-input`: single-line input
  - `run-btn`: `RUN RESEARCH` / `RUNNING...`
- Right side currently has no separate global `StatusPill`; runtime state is reflected by input/button disabled states and main panel content.

### Main Workspace (`main-area`)

Two columns:

- Left panel (`left-panel`, fixed `30%` width)
  - Agent header (`Agents`, active/waiting counters)
  - Agent list (`agent-list`): nine agent rows
    - Planning (`parse_intent`), Research, Fundamental, Macro, Sentiment, Retry Gate, Scenarios (`scenario_scoring`), Debate (`scenario_debate`), Verification (`report_finalize`)
    - each row shows dot, agent name, current action, status tag
  - Output log area
    - model-call/event stream (`log-box`)
    - shows agent-tagged LLM call messages plus system error/cancellation messages
- Right panel (`right-panel`, fluid width)
  - idle state: centered placeholder icon + state text
  - active state: report sections in a single vertical reading flow (`report-wrap`)
  - runtime footer: bottom `report-footer` with elapsed time, token/cost/source counters, and validation status

---

## 4) Report Information Architecture

Report content is progressively revealed by sections (not rendered all at once).

Section keys and render order:

1. `intent` -> Research Intent tags
2. `fundamental` -> Business/financial narrative + metric groups
3. `macro` -> macro regime/drivers/signals/risks block
4. `sentiment` -> Sentiment/price/narrative/risk blocks
5. `scenarios` -> dynamic scenario cards from backend payload, rendered with probability bars and trigger lines
6. `debate` -> calibrated scenario probabilities and debate rationale
7. `summary` -> Executive Summary (from `report_markdown`)
8. `evidence` -> Key Sources table (with reliability badges)

Validation badge (`VALID`) is attached to summary block when summary section is revealed.

---

## 5) Interaction and State Model

Core UI state variables:

- `appState`: `idle | running | complete | error`
- `query`: current input text
- `agentStatuses`: per-agent runtime lifecycle/phase/action map
- `logLines`: chronological event log
- `readySections`: streamed section registry (`section_id -> {content, source, title}`)

Run behavior:

- `runResearch()` resets lifecycle/phase logs/sections and starts a real backend stream request to `/research/stream`
- each SSE event updates one or more of:
  - agent row status/action
  - output log line
  - section reveal
  - global app state
- log auto-scrolls to latest entry

Current implementation no longer relies on local mock `STREAM`/`REPORT`; report content is rendered from backend `final` payload.
SSE parser handles `agent_status`, `llm_call`, `section_ready`, `final`, `error`, `done`.
`llm_call` entries are merged by call `id` (status progression such as `calling -> success/retry/failed`), and log lines are updated in place for the same call.
`section_ready` progressively reveals report sections (including custom sections) before stream completion.
`final` marks run `complete`, stores full report payload, and backfills any declared plan sections not yet marked ready.
`error` marks run `error` and appends a system error log.
`done` is treated as stream-termination marker only; if stream ends with `done` but without `final`/`error`, frontend treats it as interrupted/cancelled (`STREAM_DONE_WITHOUT_FINAL`).

---

## 6) Component Model

Main components in the prototype:

- `App` (state owner + orchestration)
- `AgentRow`
- `LogLine`
- report section components:
  - `SummarySection`
  - `IntentSection`
  - `EvidenceSection`
  - `FundamentalSection`
  - `MacroSection`
  - `SentimentSection`
  - `ScenariosSection`
  - `DebateSection`

Shared UI helpers:

- `SectionLabel`
- CSS utility classes for badges, cards, metric rows, scenario bars, and sentiment blocks

Runtime mapping and stream helpers:

- `AGENT_ID_BY_NAME`: backend node name -> UI row ID map (`parse_intent`, `research`, `fundamental_analysis`, `macro_analysis`, `market_sentiment`, `retry_gate`, `scenario_scoring`, `scenario_debate`, `report_finalize`)
- `applyAgentStatuses`: applies backend lifecycle/phase/action updates from `agent_status`
- `processBlock`: parses SSE blocks (`event:` + `data:`) and dispatches to state handlers
  - handled event types: `agent_status`, `llm_call`, `section_ready`, `final`, `error`, `done`

---

## 7) Production Alignment Notes

The running frontend is `src/frontend/index.html` + `src/frontend/static/styles.css`.

- The app script is currently inline in `index.html` (`type="text/babel"`)
- No `tweaks-panel.jsx` runtime module is used; the shipped frontend is `src/frontend/index.html` + `src/frontend/static/styles.css`
- Agent rows consume backend two-layer state (`lifecycle` + `phase`) instead of a single `completed`-style enum
- Runtime lifecycle from backend is `standby/active/waiting/blocked/failed`; UI derives display tags such as `ACTIVE/RUNNING/WAITING/BLOCKED/STALLED/FAILED/STANDBY` from lifecycle + recency
- `macro_analysis` and `scenario_debate` sections can also fall back to `report_json` (`report_json.macro_analysis`, `report_json.scenario_debate`) when top-level fields are absent
