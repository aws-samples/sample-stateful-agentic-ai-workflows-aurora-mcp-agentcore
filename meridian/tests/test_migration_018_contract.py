"""Migration 018 gives the backend, the gateway Lambdas and the sign-in trigger their own logins."""

import pathlib
import re

import pytest

from scripts.init_aurora_schema import split_sql

MIGRATION = (
    pathlib.Path(__file__).parent.parent / "scripts" / "migrations" / "018_service_logins.sql"
)
LOGINS = ("meridian_backend", "meridian_gateway", "meridian_identity")


@pytest.fixture
def statements():
    code = "\n".join(
        line for line in MIGRATION.read_text().splitlines() if not line.startswith("--")
    )
    return [" ".join(s.split()).lower().rstrip(";") for s in split_sql(code)]


def grants_to(statements, login):
    return [s for s in statements if s.startswith("grant") and f" to {login}" in s]


def test_the_logins_are_created_without_login_or_bypass(statements):
    create = next(s for s in statements if s.startswith("do $$") and "create role" in s)
    for login in LOGINS:
        assert f"'{login}'" in create
    assert "nologin nobypassrls noinherit nocreatedb nocreaterole" in create
    assert "if not exists (select from pg_roles where rolname = login_name)" in create


def test_a_tampered_existing_role_stops_the_migration(statements):
    create = next(s for s in statements if s.startswith("do $$") and "create role" in s)
    assert "elsif exists (" in create
    assert "rolsuper or rolbypassrls or rolinherit or rolcreatedb or rolcreaterole" in create
    assert "raise exception '% exists with attributes 018 does not allow" in create


def test_roles_are_never_altered_into_shape(statements):
    assert not any(s.startswith("alter role") for s in statements)


def test_no_password_lives_in_the_migration():
    assert "password" not in MIGRATION.read_text().lower()


def test_only_backend_and_gateway_reach_the_app_role_and_only_by_set_role(statements):
    assert [s for s in statements if "grant meridian_app to" in s] == [
        "grant meridian_app to meridian_backend with inherit false, set true",
        "grant meridian_app to meridian_gateway with inherit false, set true",
    ]


def test_the_backend_holds_reads_and_one_audit_append_and_one_function(statements):
    assert grants_to(statements, "meridian_backend") == [
        "grant meridian_app to meridian_backend with inherit false, set true",
        "grant select on traveler_identity_bindings to meridian_backend",
        "grant insert on traveler_access_audit to meridian_backend",
        "grant select on trip_packages to meridian_backend",
        "grant execute on function backend_admin_count(text, interval, text) to meridian_backend",
    ]


def test_the_gateway_holds_what_the_two_lambdas_query_and_no_function(statements):
    assert grants_to(statements, "meridian_gateway") == [
        "grant meridian_app to meridian_gateway with inherit false, set true",
        "grant select on traveler_identity_bindings to meridian_gateway",
        "grant insert on traveler_access_audit to meridian_gateway",
        "grant select on trip_packages to meridian_gateway",
    ]


def test_the_identity_login_reads_the_bindings_and_nothing_else(statements):
    assert grants_to(statements, "meridian_identity") == [
        "grant select on traveler_identity_bindings to meridian_identity",
    ]


def test_no_login_gets_a_write_beyond_the_audit_append(statements):
    for login in LOGINS:
        for grant in grants_to(statements, login):
            assert not any(w in grant for w in ("update", "delete", "truncate", "all"))
            assert "insert" not in grant or "on traveler_access_audit" in grant


def test_each_audit_insert_policy_is_for_one_login_only(statements):
    for login, name in (
        ("meridian_backend", "traveler_access_audit_backend_insert"),
        ("meridian_gateway", "traveler_access_audit_gateway_insert"),
    ):
        assert (
            f"create policy {name} on traveler_access_audit "
            f"for insert to {login} with check (true)"
        ) in statements
        assert f"drop policy if exists {name} on traveler_access_audit" in statements


def test_the_admin_count_function_is_a_pinned_definer_open_to_the_backend_only(statements):
    function = next(s for s in statements if "function backend_admin_count" in s and "create" in s)
    assert "security definer" in function
    assert "set search_path = public, pg_temp" in function
    for kind in (
        "rls_traveler_preferences", "rls_trip_interactions", "rls_conversations",
        "rls_conversation_messages", "audit_allow", "audit_deny", "agent_audit",
        "workflow_snapshots",
    ):
        assert f"'{kind}'" in function
    assert "raise exception 'unknown backend_admin_count kind: %'" in function
    signature = "backend_admin_count(text, interval, text)"
    assert f"revoke all on function {signature} from public" in statements
    executes = [s for s in statements if s.startswith(f"grant execute on function {signature}")]
    assert len(executes) == 1 and executes[0].endswith("to meridian_backend")


def test_the_function_reads_only_what_the_evidence_endpoints_count(statements):
    function = next(s for s in statements if "function backend_admin_count" in s and "create" in s)
    tables = {
        "traveler_preferences", "trip_interactions", "conversations", "conversation_messages",
        "traveler_access_audit", "agent_audit_log", "workflow_snapshots",
    }
    assert set(re.findall(r"\bfrom (\w+)", function)) == tables
    assert " insert " not in function and " update " not in function and " delete " not in function
