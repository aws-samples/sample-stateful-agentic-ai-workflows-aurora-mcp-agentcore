"""Migration 033 gives the audit export Lambda a login that can only read the audit tables."""

import pathlib

import pytest

from scripts.init_aurora_schema import split_sql

MIGRATION = (
    pathlib.Path(__file__).parent.parent / "scripts" / "migrations" / "033_audit_export_login.sql"
)
AUDIT_TABLES = ("agent_audit_log", "traveler_access_audit", "booking_action_audit")
OUT_OF_REACH = (
    "traveler_identity_bindings", "bookings", "booking_lines", "trip_packages",
    "workflow_snapshots",
)


@pytest.fixture
def statements():
    code = "\n".join(
        line for line in MIGRATION.read_text().splitlines() if not line.startswith("--")
    )
    return [" ".join(s.split()).lower().rstrip(";") for s in split_sql(code)]


@pytest.fixture
def create(statements):
    return next(s for s in statements if s.startswith("do $$") and "create role" in s)


def test_the_login_is_created_without_login_or_bypass(create):
    assert ("create role meridian_audit_export nologin nobypassrls noinherit nocreatedb "
            "nocreaterole" in create)
    assert "if not exists (select from pg_roles where rolname = 'meridian_audit_export')" in create


def test_a_tampered_existing_role_stops_the_migration(create):
    assert "rolsuper or rolbypassrls or rolinherit or rolcreatedb or rolcreaterole" in create
    assert "or rolreplication" in create
    assert ("raise exception 'meridian_audit_export exists with attributes 033 does not allow"
            in create)


def test_a_pre_existing_role_with_memberships_or_extra_grants_stops_the_migration(create):
    assert "from pg_auth_members m join pg_roles r on r.oid = m.member" in create
    assert "aclexplode(c.relacl)" in create
    for table in AUDIT_TABLES:
        assert f"'{table}'" in create
    assert "a.privilege_type = 'select'" in create
    assert ("raise exception 'meridian_audit_export exists with memberships or table grants "
            "033 does not allow") in create


def test_the_role_is_never_altered_into_shape_and_no_password_is_in_the_file(statements):
    assert all(" set " in s for s in statements if s.startswith("alter role"))
    assert not any("login" in s or "password" in s or "bypassrls" in s
                   for s in statements if s.startswith("alter role"))
    assert "password" not in MIGRATION.read_text().lower()


def test_the_login_holds_select_on_the_three_audit_tables_and_nothing_else(statements):
    grants = [s for s in statements if s.startswith("grant")]
    assert grants == [
        "grant select on agent_audit_log, traveler_access_audit, booking_action_audit "
        "to meridian_audit_export"
    ]


def test_each_audit_table_has_one_select_policy_for_the_login_that_sees_every_row(statements):
    policies = [s for s in statements if s.startswith("create policy")]
    assert sorted(policies) == sorted(
        f"create policy {table}_export_select on {table} for select to meridian_audit_export "
        "using (true)" for table in AUDIT_TABLES)
    drops = [s for s in statements if s.startswith("drop policy")]
    assert len(drops) == len(AUDIT_TABLES)
    assert all("if exists" in s for s in drops)


def test_the_login_reaches_no_other_role_and_no_bypass_policy(statements):
    assert not any(s.startswith("grant meridian") for s in statements)
    assert not any("meridian_app" in s for s in statements)
    assert not any("set role" in s or "bypassrls" in s.replace("nobypassrls", "")
                   for s in statements if not s.startswith("do $$"))


@pytest.mark.parametrize("table", OUT_OF_REACH)
def test_the_login_gets_nothing_outside_the_audit_tables(statements, table):
    assert not any(table in s for s in statements)


def test_the_login_gets_the_service_limits_and_utc(statements):
    settings = sorted(s for s in statements if s.startswith("alter role"))
    assert settings == sorted(f"alter role meridian_audit_export set {setting}" for setting in (
        "statement_timeout = '30s'", "lock_timeout = '10s'",
        "idle_in_transaction_session_timeout = '120s'", "timezone = 'utc'"))


@pytest.mark.parametrize(("name", "table", "columns"), [
    ("agent_audit_log_export_order", "agent_audit_log", "ran_at, audit_id"),
    ("traveler_access_audit_export_order", "traveler_access_audit", "decided_at, audit_id"),
    ("booking_action_audit_export_order", "booking_action_audit", "recorded_at, action_audit_id"),
])
def test_each_table_gets_an_index_in_export_order(statements, name, table, columns):
    assert f"create index if not exists {name} on {table} ({columns})" in statements


def test_the_indexes_are_not_concurrent_and_the_header_says_why():
    text = MIGRATION.read_text().lower()
    assert "concurrently" not in text.replace("rules out create index concurrently", "")
    assert "rules out create index concurrently" in text
    assert "blocks writes" in text


def test_the_migration_changes_no_table_function_or_existing_policy(statements):
    assert not any(s.startswith(("create table", "alter table", "create function",
                                 "create or replace", "revoke", "insert",
                                 "update", "delete")) for s in statements)


def test_the_index_builds_fail_fast_instead_of_queueing_writers(statements):
    lock = statements.index("set local lock_timeout = '5s'")
    first_index = next(i for i, s in enumerate(statements) if s.startswith("create index"))
    assert lock < first_index
