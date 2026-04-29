from __future__ import annotations

import re

from src.server.models.analysis import FundamentalAnalysis, MarketSentiment
from src.server.models.scenario import Scenario

_MAGNITUDE_TAG = re.compile(r"^(bearish|bullish)-[123]$|^neutral$")
SCENARIO_PROB_TOLERANCE = 0.01


def validate_scenario_scores(scenarios: list[Scenario]) -> list[str]:
    if not scenarios:
        return ["At least one scenario is required."]

    errors: list[str] = []

    total = sum(s.probability for s in scenarios)
    if abs(total - 1.0) > SCENARIO_PROB_TOLERANCE:
        errors.append(f"Scenario probabilities must sum to 1. Current sum: {total}")

    for s in scenarios:
        missing = []
        if not s.drivers:
            missing.append("drivers")
        if not s.triggers:
            missing.append("triggers")
        if missing:
            errors.append(f"Scenario '{s.name}' missing required fields: {', '.join(missing)}")
        if not any(_MAGNITUDE_TAG.match(t) for t in s.tags):
            errors.append(
                f"Scenario '{s.name}' tags must include a magnitude tag "
                f"(bearish-1..3, neutral, bullish-1..3). Got: {s.tags}"
            )

    return errors


def validate_evidence_completeness(evidence: list[dict]) -> list[str]:
    errors: list[str] = []
    required_fields = ("retrieved_at", "summary", "reliability")  # url is optional on Evidence model
    for item in evidence:
        missing = [field for field in required_fields if not item.get(field)]
        if missing:
            errors.append(f"Evidence {item.get('id', 'unknown')} missing fields: {', '.join(missing)}")
    return errors


def validate_claim_coverage(
    analysis: FundamentalAnalysis | MarketSentiment, available_evidence_ids: set[str]
) -> list[str]:
    errors: list[str] = []
    claims = analysis.claims
    for claim in claims:
        if not claim.evidence_ids:
            errors.append(f"Claim missing evidence: {claim.statement}")
            continue
        missing_refs = [ref for ref in claim.evidence_ids if ref not in available_evidence_ids]
        if missing_refs:
            errors.append(f"Claim references unknown evidence ids: {', '.join(missing_refs)}")

    return errors
