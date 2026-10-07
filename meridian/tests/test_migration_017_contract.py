"""Migration 017 widens the stop check to 'finished', without grants or data changes."""

import pathlib

import pytest

from scripts.init_aurora_schema import split_sql

MIGRATION = (
    pathlib.Path(__file__).parent.parent
    / "scripts"
    / "migrations"
    / "017_session_stop_finished.sql"
)


@pytest.fixture
def statements():
    code = "\n".join(
        line for line in MIGRATION.read_text().splitlines() if not line.startswith("--")
    )
    return [" ".join(s.split()).lower().rstrip(";") for s in split_sql(code)]


def test_the_constraint_is_dropped_and_re_added_with_three_values(statements):
    assert statements == [
        "alter table workflow_session_stops "
        "drop constraint if exists workflow_session_stops_stopped_during_check",
        "alter table workflow_session_stops "
        "add constraint workflow_session_stops_stopped_during_check "
        "check (stopped_during in ('waiting', 'running', 'finished'))",
    ]


def test_no_grant_or_policy_changes(statements):
    for word in ("grant", "revoke", "create policy", "role"):
        assert not any(word in s for s in statements), word


def test_no_data_changes(statements):
    for word in ("insert", "update", "delete", "truncate"):
        assert not any(s.startswith(word) for s in statements), word
