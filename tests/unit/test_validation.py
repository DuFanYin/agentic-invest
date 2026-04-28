"""Unit tests: validation helpers."""

import pytest
from src.server.models.analysis import (
    BusinessQuality,
    Claim,
    FundamentalAnalysis,
    Valuation,
)
from src.server.models.scenario import Scenario
from src.server.utils.validation import (
    validate_claim_coverage,
    validate_evidence_completeness,
    validate_scenario_scores,
)


def _scenario(**kwargs) -> Scenario:
    defaults = dict(
        name="Test",
        description=".",
        probability=1.0,
        drivers=["d"],
        triggers=["t"],
        evidence_ids=["ev_001"],
        tags=["neutral"],
    )
    return Scenario(**{**defaults, **kwargs})


@pytest.mark.parametrize(
    ("scenario_kwargs", "expected_markers"),
    [
        ({"probability": 0.5}, ["sum"]),
        ({"drivers": []}, ["drivers"]),
        ({"triggers": []}, ["triggers"]),
    ],
)
def test_scenario_validation_errors(scenario_kwargs, expected_markers) -> None:
    scenarios = [_scenario(**scenario_kwargs)]
    errors = validate_scenario_scores(scenarios)
    for marker in expected_markers:
        assert any(marker in e for e in errors)


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


def test_claim_coverage_flags_unknown_ids() -> None:
    analysis = FundamentalAnalysis(
        claims=[
            Claim(statement="claim A", confidence="medium", evidence_ids=["ev_999"])
        ],
        business_quality=BusinessQuality(view="stable"),
        valuation=Valuation(relative_multiple_view="fair"),
        fundamental_risks=[],
        missing_fields=[],
        metrics={},
    )
    errors = validate_claim_coverage(analysis, {"ev_001"})
    assert errors
