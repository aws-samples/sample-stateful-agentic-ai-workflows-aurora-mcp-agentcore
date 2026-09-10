"""The hold budget ceiling comes from Alex's saved budget fact, per traveler, in cents."""

from __future__ import annotations

from backend.agents.budget import (
    budget_ceiling_from_facts,
    budget_ceiling_per_traveler_cents,
)


def test_budget_fact_with_a_range_uses_the_upper_bound_per_traveler():
    facts = [{"key": "budget", "value": "Prefers $2k-3.5k per person"}]
    assert budget_ceiling_from_facts(facts, travelers=2) == 700000


def test_alex_seeded_cap_wins_over_the_per_person_range():
    facts = [
        {"key": "per_person_range", "value": "Prefers $2k-3.5k per person"},
        {"key": "budget_cap", "value": "$3,200"},
        {"key": "home_airport", "value": "JFK"},
    ]
    assert budget_ceiling_from_facts(facts, travelers=2) == 640000
    assert budget_ceiling_from_facts(facts[:1], travelers=1) == 350000


def test_plain_dollar_amount_is_read_as_is():
    facts = [{"key": "allergy", "value": "Shellfish"}, {"key": "budget", "value": "$4,000 max"}]
    assert budget_ceiling_from_facts(facts, travelers=1) == 400000


def test_no_budget_fact_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("MERIDIAN_DEFAULT_BUDGET_CEILING_CENTS", "123400")
    assert budget_ceiling_from_facts([], travelers=1) == 123400


def test_unparseable_budget_falls_back_to_the_default(monkeypatch):
    monkeypatch.delenv("MERIDIAN_DEFAULT_BUDGET_CEILING_CENTS", raising=False)
    assert budget_ceiling_from_facts([{"key": "budget", "value": "flexible"}], travelers=3) == 400000


def test_the_per_traveler_cap_is_the_party_ceiling_divided_by_the_party():
    """What the UI shows and what Cedar judges have to be the same basis."""
    facts = [
        {"key": "per_person_range", "value": "Prefers $2k-3.5k per person"},
        {"key": "budget_cap", "value": "$3,200"},
    ]
    per_traveler = budget_ceiling_per_traveler_cents(facts)
    assert per_traveler == 320000
    assert per_traveler * 2 == budget_ceiling_from_facts(facts, travelers=2)
    assert per_traveler * 5 == budget_ceiling_from_facts(facts, travelers=5)


def test_an_unsaved_budget_is_none_rather_than_the_trip_default():
    """The default is a whole-trip figure, so showing it per traveler would lie."""
    assert budget_ceiling_per_traveler_cents([]) is None
    assert budget_ceiling_per_traveler_cents([{"key": "budget", "value": "flexible"}]) is None


def test_the_governing_budget_fact_survives_confidence_ranking():
    """A reply must not deny a budget the gateway is enforcing."""
    from backend.agents.production_04.concierge import _with_budget_fact

    ranked = [{"key": "home_airport", "value": "JFK"}, {"key": "pace", "value": "slow"}]
    full = [*ranked, {"key": "budget_cap", "value": "$3,200"}]

    merged = _with_budget_fact(ranked, full)

    assert {f["key"] for f in merged} == {"home_airport", "pace", "budget_cap"}
    assert budget_ceiling_from_facts(merged, travelers=2) == 640000


def test_a_ranked_budget_fact_is_not_duplicated():
    from backend.agents.production_04.concierge import _with_budget_fact

    ranked = [{"key": "budget_cap", "value": "$3,200"}]
    assert _with_budget_fact(ranked, [*ranked, {"key": "budget", "value": "$9,000"}]) == ranked


def test_no_saved_budget_leaves_the_context_untouched():
    from backend.agents.production_04.concierge import _with_budget_fact

    ranked = [{"key": "home_airport", "value": "JFK"}]
    assert _with_budget_fact(ranked, [{"key": "pace", "value": "slow"}]) == ranked
