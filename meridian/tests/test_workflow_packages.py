"""Package helpers for the workflow's hold step."""

from backend.agents.phase_05_workflow.packages import duration_for_party


def test_hold_picks_a_duration_that_has_inventory():
    assert duration_for_party({"availability": {"2 nights": 0, "3 nights": 5}}, 1) == "3 nights"


def test_hold_skips_a_duration_with_fewer_places_than_the_party():
    package = {"availability": {"3 nights": 1, "7 nights": 4}}
    assert duration_for_party(package, 2) == "7 nights"


def test_a_duration_that_seats_exactly_the_party_is_chosen():
    assert duration_for_party({"availability": {"3 nights": 2, "7 nights": 4}}, 2) == "3 nights"


def test_no_duration_when_every_counted_duration_is_too_small_for_the_party():
    assert duration_for_party({"availability": {"3 nights": 1, "7 nights": 1}}, 2) is None


def test_no_duration_when_every_counted_duration_is_sold_out():
    sold_out = {"availability": {"3 nights": 0}, "durations": ["3 nights"]}
    assert duration_for_party(sold_out, 1) is None


def test_hold_falls_back_to_the_first_listed_size_when_nothing_is_counted():
    package = {"availability": {}, "available_sizes": ["6 nights"]}
    assert duration_for_party(package, 3) == "6 nights"
