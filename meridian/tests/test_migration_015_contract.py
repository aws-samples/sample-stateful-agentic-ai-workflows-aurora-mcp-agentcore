"""Migration 015 gives the workflow Runtime its own login and records session stops."""

import pathlib

import pytest

from scripts.init_aurora_schema import split_sql

MIGRATION = (
    pathlib.Path(__file__).parent.parent / "scripts" / "migrations" / "015_workflow_login.sql"
)


@pytest.fixture
def statements():
    code = "\n".join(
        line for line in MIGRATION.read_text().splitlines() if not line.startswith("--")
    )
    return [" ".join(s.split()).lower().rstrip(";") for s in split_sql(code)]


def test_the_login_is_created_without_login_or_bypass(statements):
    create = next(s for s in statements if "create role meridian_workflow" in s)
    assert "nologin nobypassrls noinherit nocreatedb nocreaterole" in create
    assert "if not exists (select from pg_roles where rolname = 'meridian_workflow')" in create
    assert "elsif exists (" in create
    assert "raise exception 'meridian_workflow exists with attributes" in create
    assert not any(s.startswith("alter role meridian_workflow") for s in statements)


def test_no_password_lives_in_the_migration():
    assert "password" not in MIGRATION.read_text().lower()


def test_the_login_reaches_the_app_role_only_by_set_role(statements):
    assert "grant meridian_app to meridian_workflow with inherit false, set true" in statements


def test_the_login_gets_reads_and_appends_only(statements):
    grants = [s for s in statements if s.startswith("grant") and "to meridian_workflow" in s]
    assert grants == [
        "grant meridian_app to meridian_workflow with inherit false, set true",
        "grant select on traveler_identity_bindings to meridian_workflow",
        "grant insert on traveler_access_audit to meridian_workflow",
        "grant select on trip_packages to meridian_workflow",
        "grant select on journeys, journey_threads, journey_executions to meridian_workflow",
        "grant select, insert on workflow_snapshots to meridian_workflow",
        "grant usage on sequence workflow_snapshots_snapshot_seq_seq to meridian_workflow",
    ]
    assert not any(word in g for g in grants for word in ("update", "delete", "truncate"))


def test_snapshot_policies_pin_the_traveler_and_a_bound_thread(statements):
    sql = " ".join(statements)
    for clause in (
        "create policy workflow_snapshots_workflow_select on workflow_snapshots "
        "for select to meridian_workflow using",
        "create policy workflow_snapshots_workflow_insert on workflow_snapshots "
        "for insert to meridian_workflow with check",
    ):
        assert clause in sql
    assert sql.count(
        "traveler_id = current_setting('app.current_traveler_id', true) and exists "
        "(select 1 from journey_threads jt where jt.thread_id = workflow_snapshots.session_id)"
    ) == 2


def test_the_audit_insert_policy_is_for_the_login_only(statements):
    assert (
        "create policy traveler_access_audit_workflow_insert on traveler_access_audit "
        "for insert to meridian_workflow with check (true)"
    ) in statements


def test_session_stops_are_traveler_scoped_and_append_only(statements):
    sql = " ".join(statements)
    assert "alter table workflow_session_stops enable row level security" in statements
    assert "revoke all on workflow_session_stops from meridian_app" in statements
    assert "grant select, insert on workflow_session_stops to meridian_app" in statements
    assert (
        "grant usage on sequence workflow_session_stops_stop_id_seq to meridian_app" in statements
    )
    assert "check (outcome in ('stopped', 'not_running'))" in sql
