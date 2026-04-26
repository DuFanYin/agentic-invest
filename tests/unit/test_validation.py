from src.server.models.scenario import Scenario
from src.server.utils.validation import validate_scenario_scores


def test_invalid_scenario_scores_return_error() -> None:
    scenarios = [Scenario(name="Base", description="Base case", score=0.5)]

    assert validate_scenario_scores(scenarios)
