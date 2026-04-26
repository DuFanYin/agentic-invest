"""Unit tests: validation helpers."""

from src.server.models.scenario import Scenario
from src.server.utils.validation import (
    validate_claim_coverage,
    validate_evidence_completeness,
    validate_scenario_scores,
)


def test_scenario_scores_must_sum_to_one() -> None:
    scenarios = [
        Scenario(name="Base", description="Base case", score=0.7),
        Scenario(name="Bear", description="Bear case", score=0.3),
    ]
    assert validate_scenario_scores(scenarios) == []


def test_invalid_scenario_scores_return_error() -> None:
    scenarios = [Scenario(name="Base", description="Base case", score=0.5)]
    assert validate_scenario_scores(scenarios)


def test_evidence_completeness_passes_when_all_fields_present() -> None:
    evidence = [
        {
            "id": "ev_001",
            "url": "https://example.com",
            "retrieved_at": "2026-01-01T00:00:00Z",
            "summary": "summary text",
            "reliability": "high",
        }
    ]
    assert validate_evidence_completeness(evidence) == []


def test_evidence_completeness_fails_when_url_missing() -> None:
    evidence = [
        {
            "id": "ev_001",
            "url": None,
            "retrieved_at": "2026-01-01T00:00:00Z",
            "summary": "summary text",
            "reliability": "high",
        }
    ]
    errors = validate_evidence_completeness(evidence)
    assert any("url" in e for e in errors)


def test_claim_coverage_passes_with_valid_evidence_ids() -> None:
    analysis = {
        "claims": [{"statement": "claim A", "evidence_ids": ["ev_001"]}]
    }
    errors = validate_claim_coverage(analysis, {"ev_001"})
    assert errors == []


def test_claim_coverage_fails_with_unknown_evidence_id() -> None:
    analysis = {
        "claims": [{"statement": "claim A", "evidence_ids": ["ev_999"]}]
    }
    errors = validate_claim_coverage(analysis, {"ev_001"})
    assert errors
