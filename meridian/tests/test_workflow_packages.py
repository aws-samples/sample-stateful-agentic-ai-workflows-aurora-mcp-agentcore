"""Package helpers for the workflow's hold step."""

from backend.agents.phase_05_workflow.packages import first_available_duration


def test_hold_picks_a_duration_that_has_inventory():
    assert first_available_duration({"availability": {"2 nights": 0, "3 nights": 5}}) == "3 nights"


def test_hold_falls_back_to_the_first_listed_size_when_nothing_is_counted():
    package = {"availability": {}, "available_sizes": ["6 nights"]}
    assert first_available_duration(package) == "6 nights"
