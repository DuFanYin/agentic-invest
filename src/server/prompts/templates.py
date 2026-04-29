"""All LLM prompt strings, grouped by agent and call-site name."""

from __future__ import annotations

from textwrap import dedent
from typing import NotRequired, TypedDict


class PromptSpec(TypedDict):
    system: str
    user_template: str
    schema: NotRequired[str]


PROMPTS: dict[str, dict[str, PromptSpec]] = {
    "planner": {
        "main": {
            "system": (
                "You are a senior investment research director. "
                "Your job is to read an investment question, understand what the user actually needs, "
                "and produce a structured research plan that guides a team of analysts. "
                "Focus on WHAT to research and WHY — not how to search for it. "
                "Define the strategic direction: scope, focus areas, required metrics, and report structure. "
                "Return only valid JSON — no markdown, no prose outside the JSON."
            ),
            "schema": """
Return exactly this JSON structure (no extra keys):
{
  "intent": "investment_research|comparison|scenario_analysis|risk_review|valuation_check|market_event_analysis",
  "subjects": ["..."],
  "scope": "company|sector|theme|macro|event|mixed",
  "ticker": "string|null",
  "time_horizon": "string|null",
  "research_focus": ["2-4 specific focus areas derived from the user's question"],
  "must_have_metrics": ["3-6 metric names in snake_case"],
  "plan_notes": ["2-4 specific questions or risk flags the research must address"],
  "custom_sections": [
    {
      "id": "unique_snake_case_id",
      "title": "Display Title",
      "focus": "The specific question this section must answer, written as a directive to the analyst."
    }
  ]
}

Rules:
- research_focus: be specific and actionable. Not "analyse fundamentals" but "Is the margin expansion sustainable given rising input costs?"
- must_have_metrics: name the exact metrics needed (e.g. revenue_growth_yoy, gross_margin_pct, fcf_yield).
- plan_notes: name specific risks or angles that must appear somewhere in the report. Short phrases only, max 10 words each.
- custom_sections: REQUIRED. You MUST return 1 to 3 objects in this array (never an empty array, never more than three). Each entry must address a specific angle of this query that the standard report spine does not cover. The standard template already covers: fundamentals, macro environment, market sentiment, scenarios, and scenario calibration — do not duplicate those. Each custom section must answer a question a sophisticated investor would want answered for this exact query. The id must be unique snake_case, the title a concise display label, and the focus a precise directive to the analyst writing the section.
""",
            "user_template": "User query: {query}",
        }
    },
    "research": {
        "query_planner": {
            "system": (
                "You are a tactical research analyst. "
                "Given a research plan and current evidence gaps, generate specific web search queries "
                "to fill those gaps. Each query must be concrete and directly answerable by a web search. "
                "Return only valid JSON, no markdown."
            ),
            "schema": """Return exactly this JSON (no extra keys):
{
  "queries": [
    "specific search query 1",
    "specific search query 2",
    "specific search query 3"
  ]
}
Rules:
- Return 3 to 5 queries. Never fewer than 3, never more than 5.
- Each query must be a standalone search string (not a question, not a directive).
- Queries must be diverse — cover different angles of the topic.
- If retry_question is provided, the first query must directly address it.
- Prefer recency: add year or "latest" where relevant.
- Do not repeat queries that are already covered by existing_queries.
""",
            "user_template": """SUBJECT: {subject}
RESEARCH FOCUS:
{focus_lines}
MUST-HAVE METRICS: {metrics}
RETRY QUESTION: {retry_q}
ALREADY SEARCHED (do not repeat): {existing}
""",
        }
    },
    "fundamental_analysis": {
        "main": {
            "system": (
                "You are a senior equity analyst writing for a sophisticated but non-specialist investor. "
                "Your job is to synthesise financial data into clear, insight-driven statements. "
                "Every claim must embed the actual numbers — do not separate data from interpretation. "
                "Return only valid JSON — no markdown, no prose outside the JSON."
            ),
            "schema": """
Return exactly this JSON structure (no extra keys):
{
  "claims": [
    { "statement": "...", "confidence": "high|medium|low", "evidence_ids": ["ev_001", ...] }
  ],
  "business_quality": { "view": "strong|stable|weak|deteriorating" },
  "valuation": { "relative_multiple_view": "..." },
  "fundamental_risks": [
    { "name": "...", "impact": "high|medium|low", "signal": "...", "evidence_ids": ["ev_001", ...] }
  ]
}
Rules:
- claims: 3-5 statements. Each must embed specific numbers from the metrics (e.g. "Revenue grew 22% YoY to $44.1B"). Lead with the insight, embed the data inline. No claim without a number.
- business_quality.view: one of strong|stable|weak|deteriorating.
- valuation.relative_multiple_view: one sentence with the actual multiple (e.g. "Trades at 28x forward P/E, a 15% premium to sector median").
- fundamental_risks: 1-3 risks. signal must be a specific observable indicator, not a generic phrase.
- Every claim and risk must cite at least one evidence_id from the list provided.
""",
            "user_template": """{analysis_gate_context}

INTENT: {intent_str}

RESEARCH PLAN:
Focus areas:
{focus_str}

Must-have metrics: {metrics_str}

Specific questions to address:
{notes_str}

FINANCIAL API EVIDENCE (primary source):
{ev_lines}

FINANCIAL METRICS:
{metrics_json}

SUPPLEMENTAL EVIDENCE (macro/news — for context only, do not lead with these):
{supplemental_lines}
""",
        }
    },
    "macro_analysis": {
        "main": {
            "system": (
                "You are a macro economist and market strategist writing for a sophisticated investor. "
                "Translate macro data into insight-driven statements that embed actual figures and rates. "
                "Return only valid JSON — no markdown, no prose outside the JSON."
            ),
            "schema": """
Return exactly this JSON structure (no extra keys):
{
  "macro_view": "...",
  "macro_drivers": ["...", "..."],
  "macro_risks": [
    { "name": "...", "impact": "high|medium|low", "signal": "..." }
  ],
  "rate_environment": "tightening|easing|stable",
  "growth_environment": "expanding|contracting|stable"
}
Rules:
- macro_view: one sentence that embeds a key figure (e.g. "The Fed held rates at 5.25–5.5% for the fourth consecutive meeting as core PCE remains above 3%").
- macro_drivers: 2-4 drivers, each embedding the actual rate/level/change (e.g. "10-year yield at 4.6%, up 40bps in 30 days — compressing equity multiples").
- macro_risks: 1-3 risks. signal must be a specific threshold or event to watch (e.g. "CPI re-accelerating above 3.5%").
- rate_environment: exactly one of tightening|easing|stable.
- growth_environment: exactly one of expanding|contracting|stable.
""",
            "user_template": """{analysis_gate_context}

RESEARCH CONTEXT: {intent_str}

MACRO DATA (primary source):
{macro_lines}

SUPPLEMENTAL EVIDENCE (for context only):
{supplemental_lines}
""",
        }
    },
    "market_sentiment": {
        "main": {
            "system": (
                "You are a market analyst specialising in sentiment and price action. "
                "Synthesise news and price data into insight-driven statements that embed actual figures. "
                "Return only valid JSON — no markdown, no prose outside the JSON."
            ),
            "schema": """
Return exactly this JSON structure (no extra keys):
{
  "claims": [
    { "statement": "...", "confidence": "high|medium|low", "evidence_ids": ["ev_001", ...] }
  ],
  "news_sentiment": { "direction": "positive|neutral|negative" },
  "price_action": { "return_30d_pct": 0.0, "volatility": "high|medium|low" },
  "market_narrative": { "summary": "..." },
  "sentiment_risks": [
    { "name": "...", "impact": "high|medium|low", "signal": "...", "evidence_ids": ["ev_001", ...] }
  ]
}
Rules:
- claims: 2-4 statements. Embed actual figures where available (e.g. "Stock fell 8% in 30 days on volume 2x the 90-day average, signalling institutional exit"). If price data is provided, at least one claim must reference return_30d_pct or volatility with the actual value.
- news_sentiment.direction: exactly one of positive|neutral|negative.
- news_sentiment.confidence: omit this field — not needed.
- price_action.return_30d_pct: numeric, positive = gain, negative = loss.
- market_narrative.summary: 1-2 sentences describing the dominant market story right now.
- sentiment_risks: 1-2 risks. signal must name a specific observable trigger.
- Every claim and sentiment_risk must cite at least one evidence_id.
""",
            "user_template": """{analysis_gate_context}

AVAILABLE EVIDENCE IDs: {ids_str}

NEWS HEADLINES:
{news_lines}

PRICE / MARKET DATA:
{price_str}
""",
        }
    },
    "llm_judge": {
        "analysis": {
            "system": (
                "You are a research-quality gate (strictness calibrated by research_iteration — see STRICTNESS CONTEXT). "
                "Your job is to decide whether evidence and analyses are good enough for scenario generation. "
                "You do NOT choose concrete search keywords — research expands your retry_question into queries if needed. "
                "Honor the STRICTNESS BAND alongside the Rules block: early passes tolerate fewer gaps; "
                "later passes should accept workable uncertainty unless a gap clearly blocks defensible modelling. "
                "Return only valid JSON — no markdown, no extra keys."
            ),
            "schema": """Return exactly this JSON (no extra keys):
{
  "should_retry": true,
  "retry_question": "one concrete web/news search directive, <= 20 words",
  "reason": "short reason, <= 12 words"
}
Rules:
- Default to should_retry=false.
- should_retry=true only if analyses are clearly not robust enough, the missing support is material, AND more evidence is likely obtainable in one more pass.
- Do not retry for minor thinness, normal uncertainty, or issues that report caveats can handle.
- retry_question must be an actionable search instruction (not generic).
- If should_retry=false, retry_question="".
""",
            "user_template": """SUBJECT: {subject}
HORIZON: {horizon}
SCOPE: {scope}

EVIDENCE COUNTS:
- financial_api: {fin_count}
- macro_api: {macro_count}
- news: {news_count}
- web: {web_count}
- total: {evidence_total}

ANALYSIS PRESENCE / SIGNAL:
- fundamental_present: {fa_present} (claims={fa_claims})
- macro_present: {macro_present}
- sentiment_present: {ms_present} (claims={ms_claims})

RESEARCH FOCUS:
{focus_lines}

MUST-HAVE METRICS:
{must_metrics}

STRICTNESS CONTEXT (research_iteration-aware — obey):
{strictness_guidance}
""",
        },
        "conflict": {
            "system": (
                "You weigh evidence conflicts (strictness depends on research_iteration — see STRICTNESS CONTEXT). "
                "Decide if conflicts warrant another research pass versus disclosure in-report. "
                "You do NOT resolve the conflict yourself; give one directional retry_question pointing at tension. "
                "Early iterations: escalate material conflicts sooner. Later iterations: require clearly decision-blocking conflicts. "
                "Return only valid JSON — no markdown, no extra keys."
            ),
            "schema": """Return exactly this JSON (no extra keys):
{
  "should_retry": true,
  "retry_question": "one concrete search directive to resolve the conflict, <= 20 words",
  "reason": "short reason, <= 12 words"
}
Rules:
- Default to should_retry=false.
- should_retry=true only if the conflicts are material, central to the thesis, and likely resolvable with one more search pass.
- Do not retry for normal source disagreement, small differences in framing, or conflicts that can simply be disclosed in the report.
- retry_question must target the conflict directly and be actionable.
- If should_retry=false, retry_question="".
""",
            "user_template": """SUBJECT: {subject}
HORIZON: {horizon}
SCOPE: {scope}

DETECTED CONFLICT TOPICS:
{topics}

CONFLICT DETAILS:
{conflict_lines}

STRICTNESS CONTEXT (research_iteration-aware — obey):
{strictness_guidance}
""",
        },
    },
    "scenario_scoring": {
        "main": {
            "system": (
                "You are an investment strategist. "
                "Given the analysis below, generate distinct future scenarios with probability weights. "
                "Return only valid JSON — no markdown, no prose outside the JSON."
            ),
            "schema": """
Return a JSON object with exactly one key "scenarios" containing an array of 3–5 scenario objects.
{
  "scenarios": [
    {
      "name": "...",
      "description": "...",
      "raw_probability": 0.4,
      "drivers": ["..."],
      "triggers": ["..."],
      "evidence_ids": ["ev_001", ...],
      "tags": ["bullish-2", "rate-sensitive"]
    },
    ...
  ]
}
Rules:
- name: descriptive of the future state, not bull/bear labels.
- raw_probability: estimated weight, need not sum to 1 — Python normalises.
- drivers: structural forces that make this scenario possible (2-4 items).
- triggers: specific events that would cause this scenario to play out (1-3 items).
- tags (required, at least 1): must include exactly one magnitude tag from:
    bearish-3, bearish-2, bearish-1, neutral, bullish-1, bullish-2, bullish-3
  plus any relevant domain labels (e.g. "policy-risk", "rate-sensitive").
- evidence_ids: cite at least one ID from the AVAILABLE EVIDENCE IDs list provided.
- Scenarios must represent meaningfully different causal paths.
""",
            "user_template": """AVAILABLE EVIDENCE IDs: {evidence_ids}

TICKER: {ticker} | HORIZON: {horizon}

RESEARCH PLAN (scenarios must address these focus areas):
{focus_str}

Key questions the scenarios should resolve:
{notes_str}

FUNDAMENTAL ANALYSIS:
Business quality: {fa_view}
Valuation: {fa_val}
Key claims:
{fa_claims}

MACRO ENVIRONMENT:
View: {macro_view}
Rate environment: {macro_rate}
Growth environment: {macro_growth}
Key drivers:
{macro_drivers}

MARKET SENTIMENT:
Direction: {ms_direction}
Narrative: {ms_narrative}
""",
        }
    },
    "scenario_debate": {
        "advocate": {
            "system": (
                "You are an investment analyst assigned to argue for a specific scenario. "
                "Your job is to build the strongest evidence-based case for why your assigned scenario "
                "deserves a higher or maintained probability weight, given the competition from other scenarios. "
                "Be rigorous and specific — cite evidence IDs, challenge other scenarios' claims where warranted. "
                "Return only valid JSON, no markdown, no prose outside the JSON."
            ),
            "schema": """Return this JSON (no extra keys):
{
  "scenario_name": "exact name of your assigned scenario",
  "advocacy_thesis": "2-3 sentence argument for why this scenario deserves its probability",
  "supporting_arguments": [
    "specific argument backed by evidence"
  ],
  "evidence_refs": ["ev_001", "ev_002"],
  "contested_scenarios": ["Name of scenario you argue is overweighted"]
}
Rules:
- supporting_arguments: at least 1, grounded in the provided evidence.
- evidence_refs: IDs from the available evidence list.
- contested_scenarios: list scenario names you think are overweighted and briefly why in advocacy_thesis.
- Do not adjust other scenarios' probabilities — that is the arbitrator's job.
""",
            "user_template": """YOUR ASSIGNED SCENARIO: {scenario_name}
Current probability: {probability}
Description: {description}
Drivers: {drivers}

{context}

Build the strongest case for '{scenario_name}'.
""",
        },
        "arbitrator": {
            "system": (
                "You are a senior investment committee chair conducting a scenario arbitration. "
                "You have received advocacy statements from each scenario's analyst. "
                "Probabilities are zero-sum: if one scenario goes up, others must come down. "
                "Your job is to weigh the evidence quality behind each advocacy, resolve conflicts, "
                "and produce final calibrated probabilities that are internally consistent. "
                "Return only valid JSON, no markdown, no prose outside the JSON."
            ),
            "schema": """Return this JSON (no extra keys):
{
  "debate_summary": "2-3 sentences: the key tension across scenarios and how you resolved it",
  "probability_adjustments": [
    {
      "scenario_name": "...",
      "before": 0.45,
      "after": 0.50,
      "delta": 0.05,
      "reason": "advocate's evidence was stronger than contested claims"
    }
  ],
  "calibrated_scenarios": [
    {
      "name": "...",
      "probability": 0.50
    }
  ],
  "confidence": "high|medium|low",
  "debate_flags": []
}
Hard constraints:
- calibrated_scenarios MUST include ALL scenarios — no additions, no omissions.
- Probabilities MUST sum to 1.0 exactly.
- No single scenario may move more than 0.15 from its initial probability.
- Only adjust a scenario if an advocate made a substantive evidence-backed argument for it.
- confidence: your certainty in the calibration quality given the evidence presented.
- debate_flags: include "weak_advocacy" if arguments lacked evidence, "contested" if advocates directly clashed.
""",
            "user_template": """{context}

{advocacy_blocks}

Arbitrate: weigh the evidence quality behind each advocacy and produce final probabilities.
""",
        },
    },
    "report_finalize": {
        "narrative_section": {
            "system": (
                "You are a senior investment analyst writing ONE section body of a memo-style report. "
                "Ground every substantive claim in the evidence provided — do not invent prices or KPIs. "
                "Return Markdown body text ONLY (no YAML/JSON wrappers, no preamble that names this instruction block)."
            ),
            "user_template": """Section display name (already shown above in the UI): '{section_title}'

SECTION-SPECIFIC FORMAT INSTRUCTIONS (follow exactly):
{format_instructions}

Supporting data (only use what reflects here unless common knowledge qualifies as unstated framing):
{context}

Write ONLY the prose for this section.""",
        }
    },
}


# ── Narrative subsection copy (wired into PROMPTS.report_finalize.narrative_section via {format_instructions}) ─

_STANDARD_PLAN_SECTION_IDS_FOR_NARRATIVE: frozenset[str] = frozenset(
    {
        "executive_summary",
        "fundamental_analysis",
        "macro_environment",
        "market_sentiment",
        "scenarios",
        "scenario_debate",
        "conclusion",
        "evidence",
    }
)


def narrative_section_format_instructions(section_id: str) -> str:
    """Return format rules for `_render_narrative` — kept here with other prompt copy."""

    footer = dedent(
        """\
        - Do NOT repeat the section heading: emit no Markdown '#' heading lines — the UI already shows the title.
        - Ground claims; cite IDs like [ev_001].
        - Not financial advice.
        """
    ).strip()

    conclusion_and_custom = dedent(
        """\
        - Write exactly **3–5 short paragraphs** of prose (no '#' headings, **no '-' bullet lists** unless one inline list is unavoidable).
        - Separate paragraphs with **one blank line** between blocks.
        - Each paragraph ~2–4 sentences and self-contained (~40–90 words typical); stay tight — no sprawling walls of text.
        - Cover distinct themes across paragraphs — no duplicate ideas; synthesize insight.
        - Total roughly ~150–260 words unless the topic warrants less.
        """
    ).strip()

    standard_narrative = dedent(
        """\
        - Write **3–5 short paragraphs** of prose separated by blank lines (no '#' headings unless structurally unavoidable).
        - Prefer coherent paragraphs over long bullet armies; bullets only if clearer for skimming risk lists.
        - Target ~120–240 words unless this section merits a narrower scope (e.g. executive summary pacing).
        - Keep paragraphs brisk: roughly 2–5 sentences each.
        """
    ).strip()

    executive_summary_format = dedent(
        """\
        - **Structure (required):** (1) A **short lead** — **1–2 sentences** only (bottom-line thesis; no '#' heading).
        - (2) Then **exactly 3–5** bullet lines: each line must start with `'- '` (dash + space); one crisp clause or sentence per bullet.
        - Put **one blank line** between the lead block and the first bullet line.
        - Prefer '-' bullets only (no numbered lists for these points).
        - Total roughly **120–200 words**.
        """
    ).strip()

    if section_id == "executive_summary":
        return f"{executive_summary_format}\n\n{footer}"

    if section_id == "conclusion" or section_id not in _STANDARD_PLAN_SECTION_IDS_FOR_NARRATIVE:
        return f"{conclusion_and_custom}\n\n{footer}"

    return f"{standard_narrative}\n\n{footer}"


def judge_strictness_guidance(research_iteration: int, max_research_iterations: int) -> str:
    """Increase strictness early; progressively accept 'good enough' as batches consume budget."""

    head = dedent(
        f"""\
        Pipeline context:
        - Completed research batches before this verdict: **{research_iteration}**
        - Retrieval cannot expand forever; configured hard-cap on batch count ≈ **{max_research_iterations}**.
        """
    ).strip()

    band_early = dedent(
        """\
        **Band A — earliest review (strict)**:
        - Prefer `should_retry=true` whenever evidence/analysis depth visibly under-serves fundamentals, macro lens, sentiment, or corroborating web-news relative to the stated research focus — if one more augmentation plausibly closes the gap.
        - Accept `should_retry=false` only when the dossier already supports disciplined scenario sizing with clearly cited claims.
        - Minor thinness that caveats alone can honour should **not** stand in the way of escalation here.
        """
    ).strip()

    band_middle = dedent(
        """\
        **Band B — mid pipeline (moderate)** — at least one prior augmentation already landed:
        - Relax versus Band A: residual uncertainty framed as ranges is acceptable; escalate only where omission would **materially skew** probabilistic narratives.
        - Default `should_retry=false` unless a gap is glaring and realistically fixable with one narrowly-scoped supplemental pass.
        """
    ).strip()

    band_final = dedent(
        """\
        **Band C — last discretionary review before escalation budget ends**:
        - Treat synthesis as materially complete — prefer `should_retry=false` broadly; escalate only when a gap is objectively decision-blocking AND one incremental pass reasonably repairs it.
        - Duplicated Band-A/B noise should **almost never** produce `should_retry=true` anymore.
        """
    ).strip()

    if research_iteration <= 1:
        body = band_early
    elif research_iteration >= max_research_iterations - 1:
        body = band_final
    else:
        body = band_middle

    return f"{head}\n\n{body}"
