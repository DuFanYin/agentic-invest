# Frontend Layout (Aligned with Current Code)

## 1) Overall Structure

The page uses a three-part layout:

1. Top information area (Header)
2. Main content area (Main, two columns)
3. Bottom information area (Footer)

---

## 2) Header Layout

The Header is a three-column horizontal layout:

- Left: product identity (`Investment Research` + `Research Agent`)
- Middle: query summary (single-line display of current input)
- Right: global runtime status (`Idle/Running/Complete/Error`)

---

## 3) Main Layout

Main uses a two-column layout (fixed-width sidebar on the left, primary content on the right):

- Left column (Sidebar)
  - Input card
    - Input: `query` (single input field)
    - `ticker`, `horizon`, and `risk_level` are not shown in UI and are inferred by the Orchestrator
    - Action button: `Run Research`
  - Navigation card
    - `Summary / Intent / Evidence / Analysis / Scenarios / Validation`
  - Agent Status card
    - Current status per agent (`idle/running/completed/failed`)
    - Current action (`action`)
    - Details (`details`)
    - Timeline (event timeline)
- Right column (Content)
  - Result card (main reading area)
  - Validation badge in top-right (`Not run/Valid/Needs review`)

---

## 4) Result Section Order

Inside the Result panel, content is rendered in this order:

1. Summary (`report_markdown`)
2. Intent
3. Evidence
4. Fundamental Analysis
5. Market Sentiment
6. Scenarios
7. Validation (`errors/warnings`)

---

## 5) Streaming Status Display

The frontend updates status in real time through streaming events:

- Receive `agent_status`: refresh the Agent Status panel
- Receive `state_update`: update in-progress hints (e.g. open questions)
- Receive `timeline`: append timeline entries (timestamp, event type, action)
- Receive `final`: render final report content

---

## 6) Footer Layout

Footer has two blocks:

- Left: source count (`Sources: N`)
- Right: fixed disclaimer (for research use only, not financial advice)
