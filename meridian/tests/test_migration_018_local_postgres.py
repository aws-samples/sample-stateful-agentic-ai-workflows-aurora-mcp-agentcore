"""Migration 018 against a throwaway local PostgreSQL 16+ server, run as a non-superuser owner.

Skipped unless MERIDIAN_LOCAL_PG_ADMIN_DSN names a superuser connection to a disposable local
server, for example ``postgresql://me@localhost:55432/postgres``. The test creates its own
database and owner role and drops them afterwards. It refuses to run if any meridian_* role
already exists, because migration 018 creates those roles cluster-wide.
"""

import os
import pathlib
import uuid

import psycopg
import pytest

from scripts.init_aurora_schema import split_sql

DSN = os.environ.get("MERIDIAN_LOCAL_PG_ADMIN_DSN")
MIGRATION = (
    pathlib.Path(__file__).parent.parent / "scripts" / "migrations" / "018_service_logins.sql"
)
SERVICE_ROLES = (
    "meridian_app", "meridian_backend", "meridian_gateway", "meridian_identity",
)
TRAVELERS = ("trv-a", "trv-b")
FORCED_COUNTS = {
    "rls_traveler_preferences": 2,
    "rls_trip_interactions": 2,
    "rls_conversations": 2,
    "rls_conversation_messages": 2,
}

SCHEMA = """
CREATE TABLE traveler_identity_bindings (identity TEXT, traveler_id TEXT);
CREATE TABLE trip_packages (package_id TEXT);
CREATE TABLE traveler_access_audit (
    requested_traveler_id TEXT, decision TEXT, decided_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE traveler_preferences (traveler_id TEXT);
CREATE TABLE trip_interactions (traveler_id TEXT);
CREATE TABLE conversations (conversation_id TEXT, traveler_id TEXT);
CREATE TABLE conversation_messages (conversation_id TEXT);
CREATE TABLE agent_audit_log (traveler_id TEXT, ran_at TIMESTAMPTZ DEFAULT now());
CREATE TABLE workflow_snapshots (session_id TEXT);
CREATE ROLE meridian_app NOLOGIN;
ALTER TABLE traveler_preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE traveler_preferences FORCE ROW LEVEL SECURITY;
ALTER TABLE trip_interactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE trip_interactions FORCE ROW LEVEL SECURITY;
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations FORCE ROW LEVEL SECURITY;
ALTER TABLE conversation_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_messages FORCE ROW LEVEL SECURITY;
ALTER TABLE agent_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_audit_log FORCE ROW LEVEL SECURITY;
ALTER TABLE traveler_access_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE workflow_snapshots ENABLE ROW LEVEL SECURITY;
CREATE POLICY p ON traveler_preferences FOR ALL
    USING (traveler_id = current_setting('app.current_traveler_id', true));
CREATE POLICY p ON trip_interactions FOR ALL
    USING (traveler_id = current_setting('app.current_traveler_id', true));
CREATE POLICY p ON conversations FOR ALL
    USING (traveler_id = current_setting('app.current_traveler_id', true));
CREATE POLICY p ON conversation_messages FOR ALL
    USING (conversation_id IN (SELECT conversation_id FROM conversations));
CREATE POLICY agent_audit_traveler_select ON agent_audit_log FOR SELECT TO meridian_app
    USING (traveler_id = current_setting('app.current_traveler_id', true));
CREATE POLICY p ON traveler_access_audit
    USING (requested_traveler_id = current_setting('app.current_traveler_id', true));
CREATE POLICY p ON workflow_snapshots FOR SELECT TO meridian_app
    USING (session_id = current_setting('app.current_traveler_id', true));
"""

SEED = [
    f"INSERT INTO {table} (traveler_id) VALUES ('trv-a'), ('trv-b')"
    for table in ("traveler_preferences", "trip_interactions")
] + [
    "INSERT INTO conversations VALUES ('c-a', 'trv-a'), ('c-b', 'trv-b')",
    "INSERT INTO conversation_messages VALUES ('c-a'), ('c-b')",
    "INSERT INTO agent_audit_log (traveler_id) VALUES ('trv-a'), ('trv-b'), ('trv-b')",
    "INSERT INTO traveler_access_audit (requested_traveler_id, decision) "
    "VALUES ('trv-a', 'allow'), ('trv-b', 'allow'), ('trv-b', 'deny')",
    "INSERT INTO workflow_snapshots VALUES ('s1'), ('s1'), ('s2')",
]

pytestmark = [
    pytest.mark.database,
    pytest.mark.skipif(not DSN, reason="MERIDIAN_LOCAL_PG_ADMIN_DSN names no local server"),
]


def apply_migration(cur):
    for statement in split_sql(MIGRATION.read_text()):
        cur.execute(statement)


def admin_count(conn, role, kind, window=None, key=None):
    conn.execute(f"SET ROLE {role}")
    try:
        return conn.execute(
            "SELECT backend_admin_count(%s, %s::interval, %s)", (kind, window, key)
        ).fetchone()[0]
    finally:
        conn.execute("RESET ROLE")


@pytest.fixture
def cluster():
    """A fresh database whose tables a non-superuser owner holds, with FORCE RLS applied."""
    suffix = uuid.uuid4().hex[:8]
    owner, database = f"m018_owner_{suffix}", f"m018_{suffix}"
    with psycopg.connect(DSN, autocommit=True) as admin:
        taken = admin.execute(
            "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)", (list(SERVICE_ROLES),)
        ).fetchall()
    assert not taken, f"refusing to run: {taken} already exist on this server"
    conn = None
    try:
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(f"CREATE ROLE {owner} CREATEROLE NOSUPERUSER NOBYPASSRLS")
            admin.execute(f"CREATE DATABASE {database} OWNER {owner}")
        conn = psycopg.connect(DSN, dbname=database, autocommit=True)
        conn.execute(f"SET ROLE {owner}")
        conn.execute(SCHEMA)
        apply_migration(conn)
        conn.execute("RESET ROLE")
        for statement in SEED:
            conn.execute(statement)
        yield conn, owner
    finally:
        if conn is not None:
            conn.close()
        with psycopg.connect(DSN, autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {database} WITH (FORCE)")
            for role in (*SERVICE_ROLES[1:], "meridian_app", owner):
                admin.execute(f"DROP ROLE IF EXISTS {role}")


def test_the_owner_is_not_exempt_from_row_security(cluster):
    conn, owner = cluster
    row = conn.execute(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s", (owner,)
    ).fetchone()
    assert row == (False, False)


def test_the_definer_function_counts_every_travelers_forced_rows(cluster):
    conn, _ = cluster
    for kind, expected in FORCED_COUNTS.items():
        assert admin_count(conn, "meridian_backend", kind) == expected, kind
    assert admin_count(conn, "meridian_backend", "agent_audit", "1 day") == 3


def test_the_unforced_tables_count_every_traveler_too(cluster):
    conn, _ = cluster
    assert admin_count(conn, "meridian_backend", "audit_allow", "1 day") == 2
    assert admin_count(conn, "meridian_backend", "audit_deny", "1 day") == 1
    assert admin_count(conn, "meridian_backend", "workflow_snapshots", key="s1") == 2


def test_a_login_without_execute_cannot_call_the_function(cluster):
    conn, _ = cluster
    for role in ("meridian_gateway", "meridian_identity"):
        conn.execute(f"SET ROLE {role}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT backend_admin_count('rls_conversations')")
        conn.execute("RESET ROLE")


def test_a_login_has_no_table_grant_and_only_the_owner_has_a_policy(cluster):
    conn, owner = cluster
    conn.execute("SET ROLE meridian_backend")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT count(*) FROM agent_audit_log")
    conn.execute("RESET ROLE")
    policies = conn.execute(
        "SELECT roles FROM pg_policies WHERE policyname LIKE '%_admin_count_select'"
    ).fetchall()
    assert len(policies) == 5
    for (roles,) in policies:
        assert roles == [owner]


def test_applying_the_migration_twice_keeps_one_owner_policy_per_table(cluster):
    conn, owner = cluster
    conn.execute(f"SET ROLE {owner}")
    apply_migration(conn)
    conn.execute("RESET ROLE")
    count = conn.execute(
        "SELECT count(*) FROM pg_policies WHERE policyname LIKE '%_admin_count_select'"
    ).fetchone()[0]
    assert count == 5
