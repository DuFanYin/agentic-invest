"""Unit tests: validation helpers."""

import pytest

from src.server.models.analysis import (
    BusinessQuality,
    Claim,
    Financials,
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
        name="Test", description=".", probability=1.0,
        drivers=["d"], triggers=["t"], signals=["s"], evidence_ids=["ev_001"],
        tags=["neutral"],
    )
    return Scenario(**{**defaults, **kwargs})


@pytest.mark.parametrize(
    ("scenario_kwargs", "expected_markers"),
    [
        ({"probability": 0.5}, ["sum"]),
        ({"drivers": []}, ["drivers"]),
        ({"triggers": [], "signals": []}, ["triggers", "signals"]),
    ],
)
def test_scenario_validation_errors(scenario_kwargs, expected_markers) -> None:
    scenarios = [_scenario(**scenario_kwargs)]
    errors = validate_scenario_scores(scenarios)
    for marker in expected_markers:
        assert any(marker in e for e in errors)


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
    analysis = FundamentalAnalysis(
        claims=[Claim(statement="claim A", confidence="medium", evidence_ids=evidence_ids)],
        business_quality=BusinessQuality(view="stable", drivers=[]),
        financials=Financials(profitability_trend="flat", cash_flow_quality="stable"),
        valuation=Valuation(relative_multiple_view="fair", simplified_dcf_view=""),
        fundamental_risks=[],
        missing_fields=[],
        metrics={},
    )
    errors = validate_claim_coverage(analysis, known_ids)
    if expect_errors:
        assert errors
    else:
        assert errors == []
