"""Migration 018 as applied: what the backend, gateway and identity logins can and cannot do."""

import pytest

from backend.db.rds_data_client import get_rds_data_client

pytestmark = pytest.mark.database

LOGINS = ("meridian_backend", "meridian_gateway", "meridian_identity")
TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
SEQUENCE_PRIVILEGES = ("USAGE", "SELECT", "UPDATE")
CATALOG_AND_GRANTS = {
    ("traveler_identity_bindings", "SELECT"),
    ("traveler_access_audit", "INSERT"),
    ("trip_packages", "SELECT"),
}
EXPECTED_TABLE_PRIVILEGES = {
    "meridian_backend": CATALOG_AND_GRANTS,
    "meridian_gateway": CATALOG_AND_GRANTS,
    "meridian_identity": {("traveler_identity_bindings", "SELECT")},
}
APP_ROLE_BY_SET_ONLY = [
    {"granted": "meridian_app", "admin_option": False, "inherit_option": False, "set_option": True}
]
EXPECTED_MEMBERSHIPS = {
    "meridian_backend": APP_ROLE_BY_SET_ONLY,
    "meridian_gateway": APP_ROLE_BY_SET_ONLY,
    "meridian_identity": [],
}


async def _rows(sql, params=()):
    return await get_rds_data_client().execute(sql, params)


async def _one(sql, params=()):
    return (await _rows(sql, params))[0]


@pytest.mark.parametrize("login", LOGINS)
async def test_the_login_cannot_bypass_rls_or_own_anything(login):
    row = await _one(
        "SELECT rolbypassrls, rolinherit, rolcreatedb, rolcreaterole, rolsuper "
        "FROM pg_roles WHERE rolname = %s",
        (login,),
    )
    assert row == {
        "rolbypassrls": False,
        "rolinherit": False,
        "rolcreatedb": False,
        "rolcreaterole": False,
        "rolsuper": False,
    }
    owned = await _one(
        "SELECT COUNT(*) AS n FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
        "WHERE r.rolname = %s",
        (login,),
    )
    assert owned["n"] == 0


@pytest.mark.parametrize("login", LOGINS)
async def test_the_login_reaches_other_roles_only_as_designed(login):
    rows = await _rows(
        "SELECT g.rolname AS granted, m.admin_option, m.inherit_option, m.set_option "
        "FROM pg_auth_members m "
        "JOIN pg_roles g ON g.oid = m.roleid "
        "JOIN pg_roles u ON u.oid = m.member "
        "WHERE u.rolname = %s",
        (login,),
    )
    assert rows == EXPECTED_MEMBERSHIPS[login]


@pytest.mark.parametrize("login", LOGINS)
async def test_the_login_holds_exactly_its_table_privileges(login):
    rows = await _rows(
        "SELECT t.table_name AS name, p.priv AS priv "
        "FROM information_schema.tables t "
        "CROSS JOIN unnest(string_to_array(%s, ',')) AS p(priv) "
        "WHERE t.table_schema = 'public' "
        "AND has_table_privilege(%s, 'public.' || t.table_name, p.priv)",
        (",".join(TABLE_PRIVILEGES), login),
    )
    assert {(r["name"], r["priv"]) for r in rows} == EXPECTED_TABLE_PRIVILEGES[login]


@pytest.mark.parametrize("login", LOGINS)
async def test_the_login_holds_no_sequence_privilege(login):
    rows = await _rows(
        "SELECT s.sequencename AS name, p.priv AS priv "
        "FROM pg_sequences s "
        "CROSS JOIN unnest(string_to_array(%s, ',')) AS p(priv) "
        "WHERE s.schemaname = 'public' "
        "AND has_sequence_privilege(%s, 'public.' || s.sequencename, p.priv)",
        (",".join(SEQUENCE_PRIVILEGES), login),
    )
    assert rows == []


@pytest.mark.parametrize(("login", "expected"), [
    ("meridian_backend", True), ("meridian_gateway", False), ("meridian_identity", False),
])
async def test_only_the_backend_may_call_the_admin_count_function(login, expected):
    row = await _one(
        "SELECT has_function_privilege(%s, 'backend_admin_count(text, interval, text)', "
        "'EXECUTE') AS ok",
        (login,),
    )
    assert row["ok"] is expected


async def test_the_admin_count_function_is_closed_to_everyone_else():
    row = await _one(
        "SELECT prosecdef, proconfig::text AS config, proacl::text AS acl FROM pg_proc "
        "WHERE proname = 'backend_admin_count'"
    )
    assert row["prosecdef"] is True
    assert "search_path=public, pg_temp" in row["config"]
    assert "{=" not in row["acl"] and ",=" not in row["acl"]
    assert "meridian_backend=X/" in row["acl"]


async def _policy(name):
    return await _one(
        "SELECT tablename, cmd, roles::text AS roles, qual, with_check "
        "FROM pg_policies WHERE schemaname = 'public' AND policyname = %s",
        (name,),
    )


@pytest.mark.parametrize(("name", "login"), [
    ("traveler_access_audit_backend_insert", "meridian_backend"),
    ("traveler_access_audit_gateway_insert", "meridian_gateway"),
])
async def test_each_audit_insert_policy_is_for_one_login_only(name, login):
    row = await _policy(name)
    assert row["tablename"] == "traveler_access_audit"
    assert row["cmd"] == "INSERT"
    assert row["roles"] == "{" + login + "}"
    assert row["with_check"] == "true"


async def test_the_identity_login_has_no_audit_policy():
    rows = await _rows(
        "SELECT policyname FROM pg_policies WHERE position('meridian_identity' in roles::text) > 0"
    )
    assert rows == []
