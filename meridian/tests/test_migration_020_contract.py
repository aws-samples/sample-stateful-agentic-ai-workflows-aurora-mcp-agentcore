"""Migration 020 adds one definer function that counts places left, and grants only its callers."""

import pathlib

import pytest

from scripts.init_aurora_schema import split_sql

MIGRATION = (
    pathlib.Path(__file__).parent.parent
    / "scripts"
    / "migrations"
    / "020_package_places_left.sql"
)
CALLERS = ("meridian_app", "meridian_backend", "meridian_gateway", "meridian_workflow")


@pytest.fixture
def statements():
    code = "\n".join(
        line for line in MIGRATION.read_text().splitlines() if not line.lstrip().startswith("--")
    )
    return [" ".join(s.split()).lower().rstrip(";") for s in split_sql(code)]


def test_the_function_runs_as_its_owner_with_a_pinned_search_path(statements):
    function = statements[0]
    assert function.startswith("create or replace function package_places_left(p_package_id text)")
    assert "returns jsonb" in function
    assert "security definer" in function and "stable" in function
    assert "set search_path = public, pg_temp" in function


def test_it_counts_what_create_courtesy_hold_counts(statements):
    function = statements[0]
    assert "b.status = 'confirmed'" in function
    assert "b.status = 'held' and b.hold_expires_at > current_timestamp" in function
    assert "greatest(" in function, "places left never go below zero"


def test_only_the_four_callers_may_execute_it(statements):
    assert statements[1:] == [
        "revoke all on function package_places_left(text) from public",
        "grant execute on function package_places_left(text) to " + ", ".join(CALLERS),
    ]


def test_no_table_policy_or_data_changes(statements):
    for statement in statements:
        for word in ("create policy", "alter table", "insert into", "update ", "delete from"):
            assert word not in statement, word
