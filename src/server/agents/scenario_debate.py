"""Scenario debate node — concurrent per-scenario advocates → single arbitrator."""

from __future__ import annotations

import asyncio
import json
import logging

from src.server.models.analysis import (
    FundamentalAnalysis,
    MacroAnalysis,
    MarketSentiment,
    ProbabilityAdjustment,
    ScenarioAdvocacy,
    ScenarioDebate,
)
from src.server.models.scenario import Scenario
from src.server.models.state import ResearchState
from src.server.services.llm_provider import LLMClient
from src.server.utils.contract import NODE_CONTRACTS, assert_reads, assert_writes
from src.server.utils.status import update_status
from src.server.utils.validation import SCENARIO_PROB_TOLERANCE

_READS = NODE_CONTRACTS["scenario_debate"].reads
_WRITES = NODE_CONTRACTS["scenario_debate"].writes

logger = logging.getLogger(__name__)

_default_llm = LLMClient()
_NODE = "scenario_debate"

# ── System prompts ─────────────────────────────────────────────────────────

_SYSTEM_ADVOCATE = (
    "You are an investment analyst assigned to argue for a specific scenario. "
    "Your job is to build the strongest evidence-based case for why your assigned scenario "
    "deserves a higher or maintained probability weight, given the competition from other scenarios. "
    "Be rigorous and specific — cite evidence IDs, challenge other scenarios' claims where warranted. "
    "Return only valid JSON, no markdown, no prose outside the JSON."
)

_SYSTEM_ARBITRATOR = (
    "You are a senior investment committee chair conducting a scenario arbitration. "
    "You have received advocacy statements from each scenario's analyst. "
    "Probabilities are zero-sum: if one scenario goes up, others must come down. "
    "Your job is to weigh the evidence quality behind each advocacy, resolve conflicts, "
    "and produce final calibrated probabilities that are internally consistent. "
    "Return only valid JSON, no markdown, no prose outside the JSON."
)

# ── Schemas ────────────────────────────────────────────────────────────────

_ADVOCATE_SCHEMA = """Return this JSON (no extra keys):
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
"""

_ARBITRATOR_SCHEMA = """Return this JSON (no extra keys):
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
"""

# ── Context builders ───────────────────────────────────────────────────────


def _shared_context(scenarios: list[Scenario], fa, macro, sentiment, evidence) -> str:
    scenario_lines = "\n".join(
        f"  {i + 1}. {s.name} [initial prob: {s.probability:.3f}] — {s.description[:120]}"
        for i, s in enumerate(scenarios)
    )
    evidence_ids = ", ".join(ev.id for ev in evidence) if evidence else "none"

    fa_summary = "not available"
    if isinstance(fa, FundamentalAnalysis):
        fa_summary = (
            f"Business quality: {fa.business_quality.view} | "
            f"Valuation: {fa.valuation.relative_multiple_view}"
        )

    macro_summary = "not available"
    if isinstance(macro, MacroAnalysis):
        macro_summary = (
            f"{macro.macro_view} | "
            f"Rate env: {macro.rate_environment} | "
            f"Growth env: {macro.growth_environment}"
        )

    sentiment_summary = "not available"
    if isinstance(sentiment, MarketSentiment):
        sentiment_summary = (
            f"News direction: {sentiment.news_sentiment.direction} | "
            f"Narrative: {sentiment.market_narrative.summary[:150]}"
        )

    return (
        f"ALL SCENARIOS (name [initial probability] — description):\n{scenario_lines}\n\n"
        f"Note: probabilities are zero-sum. If your scenario goes up, others must go down.\n\n"
        f"AVAILABLE EVIDENCE IDs: {evidence_ids}\n\n"
        f"FUNDAMENTAL ANALYSIS: {fa_summary}\n"
        f"MACRO ENVIRONMENT: {macro_summary}\n"
        f"MARKET SENTIMENT: {sentiment_summary}"
    )


def _advocate_prompt(scenario: Scenario, context: str) -> str:
    return (
        f"{_ADVOCATE_SCHEMA}\n\n"
        f"YOUR ASSIGNED SCENARIO: {scenario.name}\n"
        f"Current probability: {scenario.probability:.3f}\n"
        f"Description: {scenario.description}\n"
        f"Drivers: {', '.join(scenario.drivers) or 'not specified'}\n\n"
        f"{context}\n\n"
        f"Build the strongest case for '{scenario.name}'."
    )


def _arbitrator_prompt(
    scenarios: list[Scenario],
    advocacies: list[ScenarioAdvocacy],
    context: str,
) -> str:
    advocacy_blocks = []
    for adv in advocacies:
        args = "\n".join(f"    - {a}" for a in adv.supporting_arguments)
        contested = ", ".join(adv.contested_scenarios) or "none"
        advocacy_blocks.append(
            f"ADVOCATE FOR '{adv.scenario_name}':\n"
            f"  Thesis: {adv.advocacy_thesis}\n"
            f"  Arguments:\n{args}\n"
            f"  Evidence cited: {', '.join(adv.evidence_refs) or 'none'}\n"
            f"  Contests: {contested}"
        )

    return (
        f"{_ARBITRATOR_SCHEMA}\n\n"
        f"{context}\n\n"
        + "\n\n".join(advocacy_blocks)
        + "\n\nArbitrate: weigh the evidence quality behind each advocacy and produce final probabilities."
    )


# ── Validation & fallback ──────────────────────────────────────────────────


def _degraded_debate(scenarios: list[Scenario]) -> ScenarioDebate:
    return ScenarioDebate(
        debate_summary="Debate unavailable.",
        calibrated_scenarios=[
            {"name": s.name, "probability": s.probability} for s in scenarios
        ],
        confidence="low",
        debate_flags=["debate_degraded"],
        degraded=True,
    )


def _validate_and_fix(
    raw: dict,
    scenarios: list[Scenario],
    advocacies: list[ScenarioAdvocacy],
) -> ScenarioDebate:
    adjustments = []
    for adj in raw.get("probability_adjustments", []):
        before = float(adj.get("before", 0))
        after = float(adj.get("after", 0))
        delta = after - before
        if abs(delta) > 0.15:
            after = before + (0.15 if delta > 0 else -0.15)
            after = max(0.0, min(1.0, after))
            delta = round(after - before, 6)
        adjustments.append(
            ProbabilityAdjustment(
                scenario_name=adj["scenario_name"],
                before=round(before, 6),
                after=round(after, 6),
                delta=round(delta, 6),
                reason=adj.get("reason", ""),
            )
        )

    calibrated = raw.get("calibrated_scenarios", [])
    baseline_by_name = {s.name: s for s in scenarios}
    calibrated_names = {
        str(item.get("name"))
        for item in calibrated
        if isinstance(item, dict) and item.get("name")
    }
    if calibrated_names != set(baseline_by_name):
        return _degraded_debate(scenarios)

    total = sum(s.get("probability", 0) for s in calibrated) or 1.0
    if abs(total - 1.0) > SCENARIO_PROB_TOLERANCE:
        calibrated = [
            {**s, "probability": round(s.get("probability", 0) / total, 6)}
            for s in calibrated
        ]

    advocacy_summaries = [
        {
            "scenario_name": adv.scenario_name,
            "thesis": adv.advocacy_thesis,
        }
        for adv in advocacies
    ]

    return ScenarioDebate(
        debate_summary=raw.get("debate_summary", ""),
        advocacy_summaries=advocacy_summaries,
        probability_adjustments=adjustments,
        calibrated_scenarios=calibrated,
        confidence=raw.get("confidence", "medium"),
        debate_flags=raw.get("debate_flags", []),
    )


# ── Core debate logic ──────────────────────────────────────────────────────


async def _run_advocate(
    scenario: Scenario,
    context: str,
    llm: LLMClient,
) -> ScenarioAdvocacy | None:
    prompt = _advocate_prompt(scenario, context)
    try:
        raw = await llm.call_with_retry(prompt, system=_SYSTEM_ADVOCATE, node=_NODE)
        parsed = json.loads(raw)
        return ScenarioAdvocacy.model_validate(parsed)
    except Exception as exc:
        logger.warning("%s: advocate for '%s' failed — %s", _NODE, scenario.name, exc)
        return None


async def _run_debate(
    scenarios: list[Scenario],
    fa,
    macro,
    sentiment,
    evidence,
    llm: LLMClient,
    statuses: list,
) -> tuple[ScenarioDebate, list]:
    context = _shared_context(scenarios, fa, macro, sentiment, evidence)

    # Round 1: all scenario advocates run concurrently
    statuses = update_status(
        statuses,
        "scenario_debate",
        lifecycle="active",
        phase="debating_scenarios",
        action=f"advocates debating ({len(scenarios)} scenarios)",
    )
    advocate_tasks = [_run_advocate(s, context, llm) for s in scenarios]
    advocate_results = await asyncio.gather(*advocate_tasks)

    # Filter out failed advocates — partial advocacy is still usable
    advocacies = [a for a in advocate_results if a is not None]
    if not advocacies:
        logger.warning("%s: all advocates failed", _NODE)
        return _degraded_debate(scenarios), statuses

    succeeded = len(advocacies)
    total = len(scenarios)
    logger.info(
        "%s: round 1 complete — %d/%d advocates succeeded", _NODE, succeeded, total
    )

    # Round 2: single arbitrator sees all advocacy statements
    statuses = update_status(
        statuses,
        "scenario_debate",
        lifecycle="active",
        phase="debating_scenarios",
        action="arbitrator ruling",
        progress_hint=f"{succeeded}/{total} advocates",
    )
    arb_prompt = _arbitrator_prompt(scenarios, advocacies, context)
    try:
        raw = await llm.call_with_retry(
            arb_prompt, system=_SYSTEM_ARBITRATOR, node=_NODE
        )
        parsed = json.loads(raw)
        debate = _validate_and_fix(parsed, scenarios, advocacies)
        if succeeded < total:
            debate.debate_flags.append(f"partial_advocacy_{succeeded}_of_{total}")
        return debate, statuses
    except Exception as exc:
        logger.warning("%s: arbitrator failed — %s", _NODE, exc)
        return _degraded_debate(scenarios), statuses


# ── Node entry point ───────────────────────────────────────────────────────


async def scenario_debate_node(
    state: ResearchState, *, llm: LLMClient = _default_llm
) -> ResearchState:
    assert_reads(state, _READS, _NODE)

    scenarios = state.get("scenarios") or []
    evidence = state.get("evidence") or []
    fa = state.get("fundamental_analysis")
    macro = state.get("macro_analysis")
    sentiment = state.get("market_sentiment")
    statuses = list(state.get("agent_statuses") or [])

    if not scenarios:
        debate = _degraded_debate([])
    else:
        debate, statuses = await _run_debate(
            scenarios, fa, macro, sentiment, evidence, llm, statuses
        )

    flags_str = ",".join(debate.debate_flags) if debate.debate_flags else "none"
    statuses = update_status(
        statuses,
        "scenario_debate",
        lifecycle="degraded" if debate.degraded else "standby",
        phase="debating_scenarios",
        action="debate degraded" if debate.degraded else "debate complete",
        details=[
            f"scenarios={len(scenarios)}",
            f"adjustments={len(debate.probability_adjustments)}",
            f"confidence={debate.confidence}",
            f"flags={flags_str}",
        ],
    )
    statuses = update_status(
        statuses,
        "report_finalize",
        lifecycle="active",
        phase="generating_report",
        action="generating report",
    )

    delta = {"scenario_debate": debate, "agent_statuses": statuses}
    assert_writes(delta, _WRITES, "scenario_debate")
    return delta
