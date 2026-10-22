"""A publish compares all effective defaults, including unset and invalid settings."""

import pytest

from backend.agents.budget import DEFAULT_ENV, default_budget_cents
from scripts.identity_release.budget import budget_findings


@pytest.mark.parametrize("value", ["", "0", "-1", "1.5", "no", None, [], "9007199254740992"])
def test_invalid_defaults_are_refused_without_echoing_the_value(value):
    with pytest.raises(ValueError, match=DEFAULT_ENV):
        default_budget_cents({DEFAULT_ENV: value})
    assert budget_findings({"Holds Lambda": {DEFAULT_ENV: value}}) == [
        f"Holds Lambda: {DEFAULT_ENV} is not a valid fallback budget",
    ]


def test_unset_and_explicit_built_in_defaults_match():
    assert budget_findings({"backend": {}, "workflow": {DEFAULT_ENV: "400000"}, "holds": {}}) == []


@pytest.mark.parametrize("changed", ["backend", "workflow", "concierge", "holds"])
def test_any_hop_with_a_different_budget_is_refused(changed):
    environments = {name: {} for name in ("backend", "workflow", "concierge", "holds")}
    environments[changed] = {DEFAULT_ENV: "123400"}
    assert any("mismatch" in finding for finding in budget_findings(environments))


def test_an_unreadable_environment_is_refused():
    assert budget_findings({"Holds Lambda": None}) == [
        "Holds Lambda: cannot read the fallback budget environment",
    ]
