"""Migration 033 as applied: meridian_audit_export reads every audit row and can change nothing.

Needs the live cluster with 033 applied and AURORA_AUDIT_EXPORT_SECRET_ARN set by
scripts/provision_service_logins.py --login audit.
"""

import asyncio
import importlib.util
import os
import sys
import uuid
from pathlib import Path

import boto3
import pytest

from backend.db.rds_data_client import RDSDataClient, get_rds_data_client

pytestmark = pytest.mark.database

LOGIN = "meridian_audit_export"
AUDIT_TABLES = ("agent_audit_log", "traveler_access_audit", "booking_action_audit")
TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
COLUMN_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "REFERENCES")
SEQUENCE_PRIVILEGES = ("USAGE", "SELECT", "UPDATE")
APPLICATION_SCHEMAS = (
    "n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname !~ '^pg_'"
)
EXPORTER = (
    Path(__file__).resolve().parents[1] / "infra" / "functions" / "audit_export" / "audit_export.py"
)


def _secret() -> str:
    secret = os.environ.get("AURORA_AUDIT_EXPORT_SECRET_ARN")
    if not secret:
        pytest.fail("AURORA_AUDIT_EXPORT_SECRET_ARN is not set: run "
                    "scripts/provision_service_logins.py --login audit --apply "
                    "--i-understand-this-changes-aws --write-env")
    return secret


@pytest.fixture
def login() -> RDSDataClient:
    return RDSDataClient(secret_arn=_secret())


async def _master(sql, params=()):
    return await get_rds_data_client().execute(sql, tuple(params))


async def test_the_login_can_log_in_but_cannot_bypass_rls_or_own_anything():
    rows = await _master(
        "SELECT rolcanlogin, rolbypassrls, rolinherit, rolcreatedb, rolcreaterole, rolsuper, "
        "rolreplication FROM pg_roles WHERE rolname = %s", (LOGIN,))
    assert rows == [{"rolcanlogin": True, "rolbypassrls": False, "rolinherit": False,
                     "rolcreatedb": False, "rolcreaterole": False, "rolsuper": False,
                     "rolreplication": False}]
    owned = await _master(
        "SELECT (SELECT COUNT(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
        "WHERE r.rolname = %s) + "
        "(SELECT COUNT(*) FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner "
        "WHERE r.rolname = %s) AS n", (LOGIN, LOGIN))
    assert owned == [{"n": 0}]


async def test_a_fresh_session_as_the_login_has_the_service_limits_and_utc(login):
    rows = await login.execute(
        "SELECT current_setting('statement_timeout') AS statement_timeout, "
        "current_setting('lock_timeout') AS lock_timeout, "
        "current_setting('idle_in_transaction_session_timeout') AS idle, "
        "current_setting('TimeZone') AS tz")
    assert rows == [{"statement_timeout": "30s", "lock_timeout": "10s", "idle": "2min",
                     "tz": "UTC"}]


WORKLOAD_SECRETS = (
    "AURORA_BACKEND_SECRET_ARN", "AURORA_GATEWAY_SECRET_ARN", "AURORA_IDENTITY_SECRET_ARN",
    "AURORA_CATALOG_SECRET_ARN", "AURORA_WORKFLOW_SECRET_ARN", "AURORA_AUDIT_EXPORT_SECRET_ARN",
)


@pytest.mark.parametrize("variable", WORKLOAD_SECRETS)
async def test_every_workload_login_stores_timestamps_in_utc(variable):
    """The exporter compares ran_at (timestamp without time zone) with UTC by name.

    A writer whose sessions run in another zone would store local time there, and the settle
    comparison would be off by the zone's offset.
    """
    secret = os.environ.get(variable)
    if not secret:
        pytest.fail(f"{variable} is not set: provision that login first")
    rows = await RDSDataClient(secret_arn=secret).execute(
        "SELECT current_setting('TimeZone') AS tz")
    assert rows == [{"tz": "UTC"}]


async def test_the_login_is_a_member_of_no_role():
    rows = await _master(
        "SELECT g.rolname AS granted FROM pg_auth_members m "
        "JOIN pg_roles g ON g.oid = m.roleid JOIN pg_roles u ON u.oid = m.member "
        "WHERE u.rolname = %s", (LOGIN,))
    assert rows == []


async def test_the_login_holds_select_on_the_three_audit_tables_and_no_other_privilege():
    rows = await _master(
        "SELECT c.relname AS name, p.priv AS priv "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "CROSS JOIN unnest(string_to_array(%s, ',')) AS p(priv) "
        f"WHERE {APPLICATION_SCHEMAS} AND c.relkind IN ('r', 'p', 'v', 'm', 'f') "
        "AND CASE WHEN p.priv = ANY (string_to_array(%s, ',')) "
        "THEN has_any_column_privilege(%s, c.oid, p.priv) "
        "ELSE has_table_privilege(%s, c.oid, p.priv) END",
        (",".join(TABLE_PRIVILEGES), ",".join(COLUMN_PRIVILEGES), LOGIN, LOGIN))
    assert {(r["name"], r["priv"]) for r in rows} == {(t, "SELECT") for t in AUDIT_TABLES}


async def test_the_login_holds_no_sequence_privilege_and_executes_no_definer_function():
    sequences = await _master(
        "SELECT s.sequencename AS name, p.priv AS priv FROM pg_sequences s "
        "CROSS JOIN unnest(string_to_array(%s, ',')) AS p(priv) WHERE s.schemaname = 'public' "
        "AND has_sequence_privilege(%s, 'public.' || s.sequencename, p.priv)",
        (",".join(SEQUENCE_PRIVILEGES), LOGIN))
    assert sequences == []
    functions = await _master(
        "SELECT p.oid::regprocedure::text AS fn FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid = p.pronamespace "
        f"WHERE {APPLICATION_SCHEMAS} AND p.prosecdef "
        "AND has_function_privilege(%s, p.oid, 'EXECUTE')", (LOGIN,))
    assert functions == []


async def test_each_audit_table_has_one_select_policy_for_the_login_and_it_is_not_bypassrls():
    rows = await _master(
        "SELECT tablename, policyname, cmd, roles::text AS roles, qual FROM pg_policies "
        "WHERE %s = ANY (roles) ORDER BY tablename", (LOGIN,))
    assert [(r["tablename"], r["cmd"], r["qual"]) for r in rows] == [
        (t, "SELECT", "true") for t in sorted(AUDIT_TABLES)]
    assert all(r["roles"] == "{" + LOGIN + "}" for r in rows)


async def test_the_audit_tables_still_force_or_enable_row_level_security():
    # The Data API takes no array parameters, so each table name is its own parameter.
    marks = ", ".join(["%s"] * len(AUDIT_TABLES))
    rows = await _master(
        "SELECT relname, relrowsecurity FROM pg_class "
        f"WHERE relkind = 'r' AND relname IN ({marks}) ORDER BY relname", tuple(AUDIT_TABLES))
    assert len(rows) == len(AUDIT_TABLES) and all(r["relrowsecurity"] for r in rows)


@pytest.mark.parametrize("table", AUDIT_TABLES)
async def test_the_login_reads_every_travelers_rows_and_the_count_matches_the_owner(login, table):
    seen = await login.execute(f"SELECT COUNT(*) AS n FROM {table}")
    held = await _master(f"SELECT COUNT(*) AS n FROM {table}")
    assert seen == held


@pytest.mark.parametrize(("sql", "obj"), [
    ("DELETE FROM agent_audit_log WHERE false", "table agent_audit_log"),
    ("UPDATE agent_audit_log SET agent_name = agent_name WHERE false", "table agent_audit_log"),
    ("INSERT INTO traveler_access_audit (audit_id, identity_provider, subject_id, "
     "requested_traveler_id, decision) SELECT 'x', 'cognito', 'forged', 'trv_meridian_demo', "
     "'deny' WHERE false", "table traveler_access_audit"),
    ("DELETE FROM traveler_access_audit WHERE false", "table traveler_access_audit"),
    ("UPDATE booking_action_audit SET operation = operation WHERE false",
     "table booking_action_audit"),
    ("DELETE FROM booking_action_audit WHERE false", "table booking_action_audit"),
    ("TRUNCATE booking_action_audit", "table booking_action_audit"),
    ("SELECT COUNT(*) FROM traveler_identity_bindings", "table traveler_identity_bindings"),
    ("SELECT COUNT(*) FROM bookings", "table bookings"),
    ("SELECT backend_admin_count('rls_conversations')", "function backend_admin_count"),
])
async def test_the_login_cannot_write_an_audit_row_or_read_anything_else(login, sql, obj):
    with pytest.raises(Exception, match=f"permission denied for {obj}"):
        await login.execute(sql)


async def test_the_login_cannot_become_another_role(login):
    with pytest.raises(Exception, match="permission denied to set role"):
        await login.execute("SET ROLE meridian_app")


@pytest.fixture
def exporter(monkeypatch):
    """The exporter's own module, so its SQL runs here exactly as the Lambda sends it."""
    for name, value in {
        "AURORA_CLUSTER_ARN": os.environ["AURORA_CLUSTER_ARN"], "AURORA_SECRET_ARN": _secret(),
        "AURORA_DATABASE": os.getenv("AURORA_DATABASE", "meridian"),
        "AUDIT_EXPORT_BUCKET": "unused",
    }.items():
        monkeypatch.setenv(name, value)
    spec = importlib.util.spec_from_file_location("audit_export_as_its_login", EXPORTER)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def _read_all(exporter, table, settle_seconds, batch):
    """Page through the table with the exporter's SQL and fetch_rows, as the login."""
    settings = exporter.Settings(
        os.environ["AURORA_CLUSTER_ARN"], _secret(), os.getenv("AURORA_DATABASE", "meridian"),
        "unused", batch_rows=batch, settle_seconds=settle_seconds, max_batches=1)
    rds = boto3.client("rds-data", region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
    head, seen = exporter.GENESIS, []
    while True:
        rows, _ = exporter.fetch_rows(rds, settings, table, head, batch)
        if not rows:
            return seen
        seen.extend(r["wm_id"] for r in rows)
        head = exporter.ChainHead(0, "", rows[-1]["wm_ts"], rows[-1]["wm_id"])


@pytest.mark.parametrize("name", AUDIT_TABLES)
async def test_the_exporters_read_pages_every_row_once_in_database_order(exporter, name):
    """The exporter's SQL as the login: no repeat, no gap, the order Aurora sorts by."""
    table = next(t for t in exporter.TABLES if t.name == name)
    seen = await asyncio.to_thread(_read_all, exporter, table, 0, 3)
    expected = await _master(
        f"SELECT {table.id_column} AS id FROM {name} "
        f"ORDER BY {table.ts_column}, {table.id_column}")
    assert seen == [r["id"] for r in expected]


async def test_the_settle_time_holds_back_a_row_that_was_just_written(exporter):
    table = next(t for t in exporter.TABLES if t.name == "traveler_access_audit")
    audit_id = f"itest-{uuid.uuid4().hex[:12]}"
    await _master(
        "INSERT INTO traveler_access_audit (audit_id, identity_provider, subject_id, "
        "requested_traveler_id, decision) VALUES (%s, 'cognito', 'itest', 'trv_meridian_demo', "
        "'deny')", (audit_id,))
    try:
        held = await asyncio.to_thread(_read_all, exporter, table, 180, 50)
        released = await asyncio.to_thread(_read_all, exporter, table, 0, 50)
    finally:
        await _master("DELETE FROM traveler_access_audit WHERE audit_id = %s", (audit_id,))
    assert audit_id not in held
    assert audit_id in released
