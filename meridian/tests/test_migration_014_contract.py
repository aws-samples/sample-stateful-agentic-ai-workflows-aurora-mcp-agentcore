"""Migration 014 keeps workflow snapshots append-only and traveler-scoped on read."""

import pathlib

import pytest

from scripts.init_aurora_schema import split_sql

MIGRATION = (
    pathlib.Path(__file__).parent.parent / "scripts" / "migrations" / "014_workflow_snapshots.sql"
)


@pytest.fixture
def statements() -> list[str]:
    code = "\n".join(
        line for line in MIGRATION.read_text().splitlines() if not line.startswith("--")
    )
    return [" ".join(s.split()).lower().rstrip(";") for s in split_sql(code)]


@pytest.fixture
def sql(statements: list[str]) -> str:
    return " ".join(statements)


def test_the_app_role_reads_and_never_writes(statements: list[str]) -> None:
    assert "revoke all on workflow_snapshots from meridian_app" in statements
    assert "grant select on workflow_snapshots to meridian_app" in statements
    grants = [s for s in statements if s.startswith("grant") and "meridian_app" in s]
    assert all("insert" not in g and "update" not in g and "delete" not in g for g in grants)


def test_reads_are_scoped_to_the_traveler_and_a_bound_thread(sql: str) -> None:
    assert "alter table workflow_snapshots enable row level security" in sql
    assert "for select to meridian_app" in sql
    assert "traveler_id = current_setting('app.current_traveler_id', true)" in sql
    assert "journey_threads jt where jt.thread_id = workflow_snapshots.session_id" in sql


def test_status_is_derived_from_the_snapshot_itself(sql: str) -> None:
    assert "status text generated always as (snapshot #>> '{data,state,status}') stored" in sql


def test_rows_record_who_wrote_them(sql: str) -> None:
    for column in (
        "execution_id varchar(64)",
        "worker_id varchar(128)",
        "traveler_id varchar(64) not null",
    ):
        assert column in sql
