"""Audit rows name a tool by its contract, without the Gateway target prefix."""

import pytest

from backend.agents.concierge_definition import short_name
from backend.tools.tool_names import bare_tool_name


@pytest.mark.parametrize(("reported", "bare"), [
    ("MeridianHolds___create_courtesy_hold", "create_courtesy_hold"),
    ("MeridianCatalog___hybrid_trip_search", "hybrid_trip_search"),
    ("Meridian-Catalog___hybrid_trip_search", "hybrid_trip_search"),
    ("MeridianHolds____leading_underscore", "_leading_underscore"),
])
def test_one_target_prefix_is_removed(reported, bare):
    assert bare_tool_name(reported) == bare


def test_only_the_first_separator_is_a_prefix():
    assert bare_tool_name("MeridianHolds___batch___confirm") == "batch___confirm"


@pytest.mark.parametrize("name", [
    "create_courtesy_hold", "hybrid_trip_search", "", "___create_courtesy_hold",
    "MeridianHolds___",
])
def test_a_name_without_a_whole_prefix_is_unchanged(name):
    assert bare_tool_name(name) == name

NAMES = [
    "MeridianHolds___create_courtesy_hold", "MeridianCatalog___hybrid_trip_search",
    "MeridianHolds___batch___confirm", "MeridianHolds____leading_underscore",
    "create_courtesy_hold", "", "___create_courtesy_hold", "MeridianHolds___",
]


@pytest.mark.parametrize("name", NAMES)
def test_the_concierge_definition_reads_a_target_prefix_by_the_same_rule(name):
    assert short_name(name) == bare_tool_name(name)
