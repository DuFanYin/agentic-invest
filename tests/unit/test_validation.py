"""Unit tests: validation helpers."""

import pytest

from src.server.models.scenario import Scenario
from src.server.utils.validation import (
    validate_claim_coverage,
    validate_evidence_completeness,
    validate_scenario_scores,
)


def _scenario(**kwargs) -> Scenario:
    defaults = dict(
        name="Test", description=".", probability=1.0,
        drivers=["d"], triggers=["t"], signals=["s"], evidence_ids=["ev_001"],
        tags=["neutral"],
    )
    return Scenario(**{**defaults, **kwargs})


def test_invalid_scenario_probabilities_return_error() -> None:
    scenarios = [_scenario(probability=0.5)]
    errors = validate_scenario_scores(scenarios)
    assert any("sum" in e for e in errors)


def test_scenario_missing_drivers_returns_error() -> None:
    scenarios = [_scenario(drivers=[])]
    errors = validate_scenario_scores(scenarios)
    assert any("drivers" in e for e in errors)


def test_scenario_missing_critical_fields_returns_error() -> None:
    scenarios = [_scenario(triggers=[], signals=[])]
    errors = validate_scenario_scores(scenarios)
    assert any("triggers" in e for e in errors)
    assert any("signals" in e for e in errors)


# ── evidence completeness ───────────────────────────────────────────────────

@pytest.mark.parametrize("url", ["https://example.com", None])
def test_evidence_completeness_passes_for_valid_variants(url: str | None) -> None:
    evidence = [
        {
            "id": "ev_001",
            "url": url,
            "retrieved_at": "2026-01-01T00:00:00Z",
            "summary": "summary text",
            "reliability": "high",
        }
    ]
    assert validate_evidence_completeness(evidence) == []


def test_evidence_completeness_fails_when_required_field_missing() -> None:
    evidence = [
        {
            "id": "ev_001",
            "url": None,
            "retrieved_at": None,
            "summary": "",
            "reliability": None,
        }
    ]
    errors = validate_evidence_completeness(evidence)
    assert errors


# ── claim coverage ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("evidence_ids", "known_ids", "expect_errors"),
    [
        (["ev_001"], {"ev_001"}, False),
        (["ev_999"], {"ev_001"}, True),
    ],
)
def test_claim_coverage_cases(evidence_ids, known_ids, expect_errors) -> None:
    analysis = {
        "claims": [{"statement": "claim A", "evidence_ids": evidence_ids}]
    }
    errors = validate_claim_coverage(analysis, known_ids)
    if expect_errors:
        assert errors
    else:
        assert errors == []
