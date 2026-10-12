"""Migration 016 records what a presenter's stop caught, without touching grants."""

import pathlib

import pytest

from scripts.init_aurora_schema import split_sql

MIGRATION = (
    pathlib.Path(__file__).parent.parent / "scripts" / "migrations" / "016_session_stop_detail.sql"
)


@pytest.fixture
def statements():
    code = "\n".join(
        line for line in MIGRATION.read_text().splitlines() if not line.startswith("--")
    )
    return [" ".join(s.split()).lower().rstrip(";") for s in split_sql(code)]


def test_the_table_gains_exactly_three_columns(statements):
    assert statements == [
        "alter table workflow_session_stops "
        "add column if not exists stopped_during varchar(16) not null "
        "check (stopped_during in ('waiting', 'running')), "
        "add column if not exists last_step varchar(32), "
        "add column if not exists released_execution_id varchar(64)"
    ]


def test_no_default_so_a_non_empty_table_fails_loudly(statements):
    assert not any("default" in s for s in statements)


def test_no_grant_or_policy_changes(statements):
    for word in ("grant", "revoke", "create policy", "role"):
        assert not any(word in s for s in statements), word


def test_every_column_is_added_idempotently(statements):
    sql = " ".join(statements)
    assert sql.count("add column") == 3
    assert sql.count("add column if not exists") == 3
