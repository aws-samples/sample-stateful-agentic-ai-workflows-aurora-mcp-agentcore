"""The backend and the holds Lambda read a saved budget identically, from one table of cases."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from backend.agents import budget as backend_budget
from tests.budget_cases import AMOUNT_CASES, CEILING_CASES, DEFAULT, DEFAULT_CENTS, UNCLEAR

TARGET = (
    Path(__file__).resolve().parents[1]
    / "meridian_agentcore" / "agentcore" / "gateway_targets" / "meridian_holds"
)
sys.path.insert(0, str(TARGET))

import lambda_function as holds  # noqa: E402

IMPLEMENTATIONS = {
    "backend": (backend_budget.budget_ceiling_from_facts, backend_budget.parse_usd_amounts,
                backend_budget.budget_is_unclear),
    "lambda": (holds.saved_ceiling_cents, holds.parse_usd_amounts, holds.budget_is_unclear),
}


@pytest.fixture(autouse=True)
def _default_ceiling(monkeypatch):
    monkeypatch.delenv(backend_budget.DEFAULT_ENV, raising=False)
    monkeypatch.delenv(holds.DEFAULT_BUDGET_ENV, raising=False)


def _expected_party_cents(per_traveler, travelers):
    if per_traveler == UNCLEAR:
        return 0
    if per_traveler == DEFAULT:
        return DEFAULT_CENTS
    return per_traveler * travelers


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
@pytest.mark.parametrize(("name", "facts", "travelers", "per_traveler"), CEILING_CASES,
                         ids=[case[0] for case in CEILING_CASES])
def test_the_party_ceiling(implementation, name, facts, travelers, per_traveler):
    ceiling, _, unclear = IMPLEMENTATIONS[implementation]
    assert ceiling(facts, travelers) == _expected_party_cents(per_traveler, travelers)
    assert unclear(facts) is (per_traveler == UNCLEAR)


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
@pytest.mark.parametrize(("text", "amounts"), AMOUNT_CASES)
def test_the_explicit_us_dollar_amounts(implementation, text, amounts):
    _, parse, _ = IMPLEMENTATIONS[implementation]
    assert parse(text) == amounts


def test_the_default_still_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv(backend_budget.DEFAULT_ENV, "123400")
    monkeypatch.setenv(holds.DEFAULT_BUDGET_ENV, "123400")
    assert backend_budget.budget_ceiling_from_facts([], 2) == 123400
    assert holds.saved_ceiling_cents([], 2) == 123400


@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_a_non_finite_amount_is_unclear_and_never_raises(monkeypatch, implementation):
    """Defense in depth: even a parser that returned infinity cannot crash a caller."""
    ceiling, _, unclear = IMPLEMENTATIONS[implementation]
    for module in (backend_budget, holds):
        monkeypatch.setattr(module, "parse_usd_amounts", lambda _text: [float("inf")])
    facts = [{"key": "budget_cap", "value": "$75"}]
    assert ceiling(facts, 2) == 0
    assert unclear(facts) is True
