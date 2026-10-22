"""Migration 019 adds one column-level grant so the workflow login can lock its execution row."""

import pathlib

import pytest

from scripts.init_aurora_schema import split_sql

MIGRATION = (
    pathlib.Path(__file__).parent.parent
    / "scripts"
    / "migrations"
    / "019_snapshot_fence_lock.sql"
)


@pytest.fixture
def statements():
    code = "\n".join(
        line for line in MIGRATION.read_text().splitlines() if not line.startswith("--")
    )
    return [" ".join(s.split()).lower().rstrip(";") for s in split_sql(code)]


def test_the_only_statement_is_a_column_grant_to_the_workflow_login(statements):
    assert statements == [
        "grant update (lease_expires_at) on journey_executions to meridian_workflow"
    ]


def test_no_table_wide_update_and_no_other_role(statements):
    assert not any("grant update on" in s for s in statements)
    assert not any("meridian_app" in s or "meridian_backend" in s for s in statements)
