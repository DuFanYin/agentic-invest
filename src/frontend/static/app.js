const runButton = document.getElementById("run-btn");
const queryInput = document.getElementById("query");
const resultBox = document.getElementById("result");
const statusText = document.getElementById("status");
const questionSummary = document.getElementById("question-summary");
const validationBadge = document.getElementById("validation-badge");
const sourceCount = document.getElementById("source-count");
const agentStatusPanel = document.getElementById("agent-status-panel");
const agentTimeline = document.getElementById("agent-timeline");

const DEFAULT_AGENT_STATUSES = [
  { agent: "orchestrator", status: "idle", action: "waiting", details: [] },
  { agent: "research", status: "idle", action: "waiting", details: [] },
  { agent: "fundamental_analysis", status: "idle", action: "waiting", details: [] },
  { agent: "market_sentiment", status: "idle", action: "waiting", details: [] },
  { agent: "scenario_scoring", status: "idle", action: "waiting", details: [] },
  { agent: "report_verification", status: "idle", action: "waiting", details: [] },
];
let timelineEntries = [];

function setStatus(label, state = "idle") {
  statusText.textContent = label;
  statusText.dataset.state = state;
}

function setValidationBadge(result) {
  if (!result) {
    validationBadge.textContent = "Not run";
    validationBadge.dataset.state = "idle";
    return;
  }

  validationBadge.textContent = result.is_valid ? "Valid" : "Needs review";
  validationBadge.dataset.state = result.is_valid ? "valid" : "invalid";
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderList(items, emptyText) {
  if (!items || items.length === 0) {
    return `<p class="muted">${emptyText}</p>`;
  }

  return `
    <ul class="item-list">
      ${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
    </ul>
  `;
}

function renderAgentStatuses(statuses = DEFAULT_AGENT_STATUSES) {
  agentStatusPanel.innerHTML = statuses
    .map((item) => {
      const details =
        item.details && item.details.length
          ? `<ul class="agent-details">${item.details.map((detail) => `<li>${escapeHtml(detail)}</li>`).join("")}</ul>`
          : "";

      return `
        <article class="agent-status-item">
          <div class="agent-status-head">
            <span class="agent-name">${escapeHtml(item.agent)}</span>
            <span class="agent-state" data-state="${escapeHtml(item.status)}">${escapeHtml(item.status)}</span>
          </div>
          <div class="agent-action">${escapeHtml(item.action || "waiting")}</div>
          ${details}
        </article>
      `;
    })
    .join("");
}

function formatTimelineTimestamp(value) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "--:--:--";
  }
  return parsed.toLocaleTimeString("zh-CN", { hour12: false });
}

function appendTimelineEntry(text, timestamp = new Date().toISOString()) {
  timelineEntries.push({ text, timestamp });
  if (timelineEntries.length > 80) {
    timelineEntries = timelineEntries.slice(-80);
  }
  renderTimeline();
}

function resetTimeline() {
  timelineEntries = [];
  renderTimeline();
}

function renderTimeline() {
  if (timelineEntries.length === 0) {
    agentTimeline.innerHTML = '<p class="muted">No timeline yet.</p>';
    return;
  }
  agentTimeline.innerHTML = timelineEntries
    .map(
      (entry) => `
        <article class="timeline-item">
          <div class="timeline-time">${escapeHtml(formatTimelineTimestamp(entry.timestamp))}</div>
          <div class="timeline-text">${escapeHtml(entry.text)}</div>
        </article>
      `,
    )
    .join("");
}

function renderScenarios(scenarios = []) {
  if (scenarios.length === 0) {
    return '<p class="muted">No scenarios returned.</p>';
  }

  const totalScore = scenarios.reduce((sum, scenario) => sum + Number(scenario.score || 0), 0);
  const scoreWarning =
    Math.abs(totalScore - 1) < 1e-6
      ? ""
      : `<p class="warning">Scenario scores sum to ${totalScore.toFixed(4)}, expected 1.</p>`;

  return `
    ${scoreWarning}
    <div class="scenario-grid">
      ${scenarios
        .map(
          (scenario) => `
            <article class="scenario-card">
              <div class="scenario-score">${Number(scenario.score || 0).toFixed(2)}</div>
              <h4>${escapeHtml(scenario.name)}</h4>
              <p>${escapeHtml(scenario.description)}</p>
              ${renderList(scenario.triggers, "No triggers yet.")}
            </article>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderEvidence(evidence = []) {
  if (evidence.length === 0) {
    return '<p class="muted">No evidence returned.</p>';
  }

  return `
    <div class="evidence-list">
      ${evidence
        .map(
          (item) => `
            <article class="evidence-card">
              <h4>${escapeHtml(item.title)}</h4>
              <p>${escapeHtml(item.summary)}</p>
              <div class="meta">
                <span>${escapeHtml(item.source_type)}</span>
                <span>${escapeHtml(item.reliability)}</span>
              </div>
            </article>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderResult(data) {
  const intent = data.intent || {};
  const validation = data.validation_result || {};
  const evidence = data.evidence || [];
  const scenarios = data.scenarios || [];
  const fundamentalAnalysis = data.fundamental_analysis || data.report_json?.fundamental_analysis || {};
  const marketSentiment = data.market_sentiment || data.report_json?.market_sentiment || {};
  const agentStatuses = data.agent_statuses || DEFAULT_AGENT_STATUSES;

  sourceCount.textContent = `Sources: ${evidence.length}`;
  setValidationBadge(validation);
  renderAgentStatuses(agentStatuses);

  resultBox.className = "result";
  resultBox.innerHTML = `
    <section class="result-section">
      <h3>Summary</h3>
      <pre>${escapeHtml(data.report_markdown || "No report returned.")}</pre>
    </section>

    <section class="result-section">
      <h3>Intent</h3>
      <pre>${escapeHtml(JSON.stringify(intent, null, 2))}</pre>
    </section>

    <section class="result-section">
      <h3>Evidence</h3>
      ${renderEvidence(evidence)}
    </section>

    <section class="result-section">
      <h3>Fundamental Analysis</h3>
      <pre>${escapeHtml(JSON.stringify(fundamentalAnalysis, null, 2))}</pre>
    </section>

    <section class="result-section">
      <h3>Market Sentiment</h3>
      <pre>${escapeHtml(JSON.stringify(marketSentiment, null, 2))}</pre>
    </section>

    <section class="result-section">
      <h3>Scenarios</h3>
      ${renderScenarios(scenarios)}
    </section>

    <section class="result-section">
      <h3>Validation</h3>
      ${renderList(validation.errors, "No validation errors.")}
      ${renderList(validation.warnings, "No validation warnings.")}
    </section>
  `;
}

async function streamResearch(payload) {
  const response = await fetch("/research/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok || !response.body) {
    const fallback = await response.json().catch(() => ({}));
    throw new Error(fallback.detail || "Research stream request failed.");
  }

  const decoder = new TextDecoder();
  const reader = response.body.getReader();
  let buffer = "";
  let finalData = null;

  const processEventBlock = (block) => {
    const lines = block.split("\n");
    let eventName = "message";
    let dataPayload = "";

    for (const line of lines) {
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataPayload += line.slice(5).trim();
      }
    }

    if (!dataPayload) {
      return;
    }

    let parsed;
    try {
      parsed = JSON.parse(dataPayload);
    } catch (_error) {
      return;
    }

    if (eventName === "agent_status") {
      renderAgentStatuses(parsed);
      return;
    }

    if (eventName === "timeline") {
      if (parsed.event === "agent_status" && Array.isArray(parsed.payload)) {
        parsed.payload
          .filter((item) => item.status !== "idle")
          .forEach((item) => {
            appendTimelineEntry(
              `${item.agent}: ${item.status} - ${item.action || "waiting"}`,
              parsed.timestamp,
            );
          });
      } else if (parsed.event === "state_update") {
        const openQuestions = parsed.payload?.research_state?.open_questions || [];
        appendTimelineEntry(
          `state update: open_questions=${openQuestions.length}`,
          parsed.timestamp,
        );
      }
      return;
    }

    if (eventName === "state_update") {
      const state = parsed.research_state || {};
      const openQuestions = state.open_questions || [];
      if (openQuestions.length > 0) {
        setStatus(`Running (${openQuestions.length} open questions)`, "running");
      }
      return;
    }

    if (eventName === "final") {
      finalData = parsed;
      renderResult(parsed);
      setStatus("Complete", "complete");
    }
  };

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });

    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const eventBlock = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      processEventBlock(eventBlock);
      boundary = buffer.indexOf("\n\n");
    }
  }

  if (!finalData) {
    throw new Error("Stream completed without final report.");
  }
}

async function runResearch() {
  const query = queryInput.value.trim();

  if (!query) {
    resultBox.className = "result-empty";
    resultBox.textContent = "请先输入研究问题。";
    return;
  }

  runButton.disabled = true;
  questionSummary.textContent = query;
  setStatus("Running", "running");
  setValidationBadge(null);
  resetTimeline();
  appendTimelineEntry("run started");
  renderAgentStatuses([
    { agent: "orchestrator", status: "running", action: "parsing query", details: [] },
    { agent: "research", status: "idle", action: "waiting", details: [] },
    { agent: "fundamental_analysis", status: "idle", action: "waiting", details: [] },
    { agent: "market_sentiment", status: "idle", action: "waiting", details: [] },
    { agent: "scenario_scoring", status: "idle", action: "waiting", details: [] },
    { agent: "report_verification", status: "idle", action: "waiting", details: [] },
  ]);
  resultBox.className = "result-empty";
  resultBox.textContent = "处理中...";

  try {
    const payload = { query };
    await streamResearch(payload);
  } catch (error) {
    resultBox.className = "result-empty error";
    resultBox.textContent = `请求失败: ${error.message}`;
    renderAgentStatuses([
      { agent: "orchestrator", status: "failed", action: "request failed", details: [error.message] },
      { agent: "research", status: "idle", action: "not started", details: [] },
      { agent: "fundamental_analysis", status: "idle", action: "not started", details: [] },
      { agent: "market_sentiment", status: "idle", action: "not started", details: [] },
      { agent: "scenario_scoring", status: "idle", action: "not started", details: [] },
      { agent: "report_verification", status: "idle", action: "not started", details: [] },
    ]);
    setStatus("Error", "error");
    appendTimelineEntry(`run failed: ${error.message}`);
  } finally {
    runButton.disabled = false;
  }
}

renderAgentStatuses();
renderTimeline();
runButton.addEventListener("click", runResearch);
queryInput.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    runResearch();
  }
});
