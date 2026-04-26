from src.server.models.scenario import Scenario
from src.server.utils.validation import validate_scenario_scores


def test_scenario_scores_must_sum_to_one() -> None:
    scenarios = [
        Scenario(name="Base", description="Base case", score=0.7),
        Scenario(name="Bear", description="Bear case", score=0.3),
    ]

    assert validate_scenario_scores(scenarios) == []
