# Frontend Layout (Source of Truth: `Investment Research Agent.html`)

## 1) Overview

This frontend design is a single-page React prototype mounted into `#root`, with a dense two-pane workstation layout:

1. Top command bar (`top-bar`)
2. Main workspace (`main-area`) split into left operations panel and right report panel
3. Conditional report footer (shown when run completes)

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
- Right: global runtime `StatusPill` (`idle/running/complete/error`)

### Main Workspace (`main-area`)

Two columns:

- Left panel (`left-panel`, fixed `30%` width)
  - Agent header (`Agents`, active/waiting counters)
  - Agent list (`agent-list`): six agent rows
    - Orchestrator, Research, Fundamental, Sentiment, Scenarios, Verification
    - each row shows dot, agent name, current action, status tag
  - Output log area
    - timeline-like event stream (`log-box`)
    - shows agent-tagged messages and section-reveal system messages
- Right panel (`right-panel`, fluid width)
  - idle state: centered placeholder icon + state text
  - active state: report sections in a single vertical reading flow (`report-wrap`)
  - complete state: bottom `report-footer` with source count and disclaimer

---

## 4) Report Information Architecture

Report content is progressively revealed by sections (not rendered all at once).

Section keys and render order:

1. `summary` -> Executive Summary
2. `intent` -> Research Intent tags
3. `evidence` -> Key Sources cards (with reliability badges)
4. `fundamental` -> Business/financial narrative + metric groups
5. `sentiment` -> Sentiment/price/narrative/risk blocks
6. `scenarios` -> Bull/Base/Bear cards with probability bars and trigger lines

Validation badge (`VALID`) is attached to summary block when summary section is revealed.

---

## 5) Interaction and State Model

Core UI state variables:

- `appState`: `idle | running | complete | error`
- `query`: current input text
- `agentStatuses`: per-agent runtime lifecycle/phase/action map
- `logLines`: chronological event log
- `sections`: set of currently revealed report sections

Run behavior:

- `runResearch()` resets lifecycle/phase logs/sections and starts a real backend stream request to `/research/stream`
- each SSE event updates one or more of:
  - agent row status/action
  - output log line
  - section reveal
  - global app state
- log auto-scrolls to latest entry

Current implementation no longer relies on local mock `STREAM`/`REPORT`; report content is rendered from backend `final` payload.

---

## 6) Component Model

Main components in the prototype:

- `App` (state owner + orchestration)
- `StatusPill`
- `AgentRow`
- `LogLine`
- report section components:
  - `SummarySection`
  - `IntentSection`
  - `EvidenceSection`
  - `FundamentalSection`
  - `SentimentSection`
  - `ScenariosSection`

Shared UI helpers:

- `SectionLabel`
- CSS utility classes for badges, cards, metric rows, scenario bars, and sentiment blocks

Runtime mapping and stream helpers:

- `AGENT_ID_BY_NAME`: backend agent name -> UI row ID (`O/R/F/M/S/V`)
- `applyAgentStatuses`: applies backend lifecycle/phase/action updates from `agent_status`
- `processBlock`: parses SSE blocks (`event:` + `data:`) and dispatches to state handlers

---

## 7) Production Alignment Notes

The running frontend is `src/frontend/index.html` + `src/frontend/static/styles.css`.

- The app script is currently inline in `index.html` (`type="text/babel"`)
- `tweaks-panel.jsx` is prototype design tooling and is not included in runtime frontend
- Agent rows consume backend two-layer state (`lifecycle` + `phase`) instead of a single `completed`-style enum
- Tag semantics are execution-oriented (`ACTIVE/WAITING/STALLED/FAILED/STANDBY`) while `action` carries fine-grained task detail
