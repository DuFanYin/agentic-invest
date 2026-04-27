"""Unit tests: validation helpers."""

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


def test_scenario_probabilities_must_sum_to_one() -> None:
    scenarios = [
        _scenario(name="Down", probability=0.3, tags=["bearish-1"]),
        _scenario(name="Base", probability=0.7, tags=["neutral"]),
    ]
    assert validate_scenario_scores(scenarios) == []


def test_invalid_scenario_probabilities_return_error() -> None:
    scenarios = [_scenario(probability=0.5)]
    errors = validate_scenario_scores(scenarios)
    assert any("sum" in e for e in errors)


def test_scenario_missing_drivers_returns_error() -> None:
    scenarios = [_scenario(drivers=[])]
    errors = validate_scenario_scores(scenarios)
    assert any("drivers" in e for e in errors)


def test_scenario_missing_triggers_returns_error() -> None:
    scenarios = [_scenario(triggers=[])]
    errors = validate_scenario_scores(scenarios)
    assert any("triggers" in e for e in errors)


def test_scenario_missing_signals_returns_error() -> None:
    scenarios = [_scenario(signals=[])]
    errors = validate_scenario_scores(scenarios)
    assert any("signals" in e for e in errors)


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


def test_evidence_completeness_passes_when_url_missing() -> None:
    evidence = [
        {
            "id": "ev_001",
            "url": None,
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
