from src.server.models.scenario import Scenario


def validate_scenario_scores(scenarios: list[Scenario]) -> list[str]:
    if not scenarios:
        return ["At least one scenario is required."]

    total = sum(scenario.score for scenario in scenarios)
    if abs(total - 1) >= 1e-6:
        return [f"Scenario scores must sum to 1. Current sum: {total}"]

    return []


def validate_evidence_completeness(evidence: list[dict]) -> list[str]:
    errors: list[str] = []
    required_fields = ("url", "retrieved_at", "summary", "reliability")
    for item in evidence:
        missing = [field for field in required_fields if not item.get(field)]
        if missing:
            errors.append(f"Evidence {item.get('id', 'unknown')} missing fields: {', '.join(missing)}")
    return errors


def validate_claim_coverage(analysis: dict, available_evidence_ids: set[str]) -> list[str]:
    errors: list[str] = []
    for claim in analysis.get("claims", []):
        claim_evidence = claim.get("evidence_ids", [])
        if not claim_evidence:
            errors.append(f"Claim missing evidence: {claim.get('statement', 'unknown')}")
            continue
        missing_refs = [ref for ref in claim_evidence if ref not in available_evidence_ids]
        if missing_refs:
            errors.append(f"Claim references unknown evidence ids: {', '.join(missing_refs)}")
    return errors
