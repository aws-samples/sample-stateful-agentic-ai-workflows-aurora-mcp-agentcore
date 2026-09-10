"""The hold budget ceiling comes from Alex's saved budget fact, per traveler, in cents."""

from __future__ import annotations

from backend.agents.production_04.budget import budget_ceiling_from_facts


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
