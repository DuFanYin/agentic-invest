# Frontend Layout (Source of Truth: `src/frontend/index.html`)

## 1) Overview

This frontend design is a single-page React prototype mounted into `#root`, with a dense two-pane workstation layout:

1. Top command bar (`top-bar`)
2. Main workspace (`main-area`) split into left operations panel and right report panel
3. Runtime report footer (shown during running/complete/error states)

The interface is intentionally operator-style: compact typography, status-driven UI, and section-based report rendering.

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
    - Planning (`parse_intent`), Research, Fundamental, Macro, Sentiment, LLM Judge, Scenarios (`scenario_scoring`), Debate (`scenario_debate`), Verification (`report_finalize`)
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

The report is rendered as a section-based flow driven by backend payloads:

- `report_json.report_plan.sections` defines the standard section order
- `section_ready` marks sections as ready to render
- `narrative_sections` provides LLM-written markdown for narrative sections
- structured sections are rendered from typed payloads such as `fundamental_analysis`, `macro_analysis`, `market_sentiment`, `scenarios`, `scenario_debate`, and `evidence`
- planning can also add query-specific `custom_sections`

Typical render order:

1. intent card (`IntentSection`)
2. standard plan sections in backend-provided order
3. arrived custom sections
4. pending custom-section skeletons
5. evidence table last (once the run is complete)

Validation badge (`VALID` / `REVIEW`) is attached to the executive summary block when that section is rendered.

---

## 5) Interaction and State Model

Core UI state variables:

- `appState`: `idle | running | complete | error`
- `query`: current input text
- `agentStatuses`: per-agent runtime lifecycle/phase/action map
- `logLines`: chronological event log
- `readySections`: streamed section registry (`section_id -> {content, source, title}`)
- `reportData`: final structured response payload
- `llmCalls`: merged real-time LLM call history

Run behavior:

- `runResearch()` resets local UI state and starts the real backend stream at `/research/stream`
- log auto-scrolls to the newest entry
- the SSE parser handles `agent_status`, `llm_call`, `section_ready`, `final`, `error`, `done`

Event effects:

- `agent_status`: updates agent row status/action
- `llm_call`: merges call updates by `id` and updates the visible log line in place
- `section_ready`: reveals ready sections; structured sections may use this as a readiness signal with empty content
- `final`: stores the full response payload, merges final `llm_calls`, backfills undeclared standard-plan sections, and marks the run `complete`
- `error`: marks the run `error` and appends a system error log
- `done`: acts only as a termination marker; if it arrives without `final`/`error`, the frontend treats the run as interrupted (`STREAM_DONE_WITHOUT_FINAL`)

---

## 6) Component Model

Main components:

- `App` (state owner + stream orchestration)
- `AgentRow`
- `LogLine`
- report section components: `SummarySection`, `IntentSection`, `EvidenceSection`, `FundamentalSection`, `MacroSection`, `SentimentSection`, `ScenariosSection`, `DebateSection`
- shared UI helper: `SectionLabel`
- CSS utility classes for badges, cards, metric rows, scenario bars, and sentiment blocks

Runtime mapping and stream helpers:

- `AGENT_ID_BY_NAME`: backend node name -> UI row ID map (`parse_intent`, `research`, `fundamental_analysis`, `macro_analysis`, `market_sentiment`, `llm_judge`, `scenario_scoring`, `scenario_debate`, `report_finalize`)
- `applyAgentStatuses`: applies backend lifecycle/phase/action updates from `agent_status`
- `processBlock`: parses SSE blocks (`event:` + `data:`) and dispatches to state handlers
  - handled event types: `agent_status`, `llm_call`, `section_ready`, `final`, `error`, `done`