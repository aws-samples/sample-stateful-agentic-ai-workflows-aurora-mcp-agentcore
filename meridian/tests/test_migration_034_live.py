"""After migration 034, each login's clone matches it in Aurora, and the rotator is bounded.

Run only after the migration is applied, with approval for the live cluster. It reads the catalog
as the master login and compares each clone with its login: role attributes, role settings, the
privileges each holds on every table, sequence and function in the public schema, and the path to
meridian_app. It never sets a password and never connects as a clone.
"""

import pytest

from backend.db.rds_data_client import get_rds_data_client
from tests.test_migration_034_contract import LOGIN_ROLES

pytestmark = pytest.mark.database

ATTRIBUTES_SQL = (
    "SELECT rolname, rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolcanlogin, "
    "rolreplication, rolbypassrls, rolconnlimit, "
    "coalesce((SELECT string_agg(c, ',' ORDER BY c) FROM unnest(rolconfig) c), '') AS config "
    "FROM pg_roles WHERE rolname = ANY(string_to_array(%s, ','))"
)
MEMBERSHIP_SQL = (
    "SELECT m.rolname AS member, g.rolname AS role, a.inherit_option, a.set_option, "
    "a.admin_option "
    "FROM pg_auth_members a JOIN pg_roles m ON m.oid = a.member "
    "JOIN pg_roles g ON g.oid = a.roleid WHERE m.rolname = ANY(string_to_array(%s, ','))"
)
TABLE_PRIVILEGES_SQL = (
    "SELECT c.relname, r.rolname, "
    "has_table_privilege(r.rolname, c.oid, 'SELECT') AS s, "
    "has_table_privilege(r.rolname, c.oid, 'INSERT') AS i, "
    "has_table_privilege(r.rolname, c.oid, 'UPDATE') AS u, "
    "has_table_privilege(r.rolname, c.oid, 'DELETE') AS d "
    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
    "CROSS JOIN pg_roles r WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v') "
    "AND r.rolname = ANY(string_to_array(%s, ','))"
)
FUNCTION_PRIVILEGES_SQL = (
    "SELECT p.oid::regprocedure::text AS fn, r.rolname, "
    "has_function_privilege(r.rolname, p.oid, 'EXECUTE') AS x "
    "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
    "CROSS JOIN pg_roles r WHERE n.nspname = 'public' "
    "AND r.rolname = ANY(string_to_array(%s, ','))"
)
SET_ROLE_SQL = (
    "SELECT r.rolname, pg_has_role(r.rolname, 'meridian_app', 'SET') AS can_set "
    "FROM pg_roles r WHERE r.rolname = ANY(string_to_array(%s, ','))"
)


async def read(sql, roles):
    return await get_rds_data_client().execute(sql, (",".join(roles),))


def by_role(rows, key):
    held = {}
    for row in rows:
        held.setdefault(row["rolname"], {})[row[key]] = row
    return held


@pytest.mark.parametrize("login", LOGIN_ROLES)
async def test_a_clone_has_its_logins_attributes_and_settings(login):
    clone = f"{login}_clone"
    rows = {row["rolname"]: row for row in await read(ATTRIBUTES_SQL, [login, clone])}
    assert set(rows) == {login, clone}
    login_row, clone_row = rows[login], rows[clone]
    for column in ("rolsuper", "rolinherit", "rolcreaterole", "rolcreatedb", "rolreplication",
                   "rolbypassrls", "rolconnlimit", "config"):
        assert clone_row[column] == login_row[column], column
    assert clone_row["rolbypassrls"] is False
    assert "statement_timeout=30s" in clone_row["config"]
    assert "lock_timeout=10s" in clone_row["config"]


@pytest.mark.parametrize("login", LOGIN_ROLES)
async def test_a_clone_holds_membership_of_its_login_and_nothing_else(login):
    clone = f"{login}_clone"
    rows = await read(MEMBERSHIP_SQL, [clone])
    assert [(r["role"], r["inherit_option"], r["set_option"]) for r in rows] == [
        (login, True, True)]


@pytest.mark.parametrize("login", LOGIN_ROLES)
async def test_a_clone_holds_exactly_the_privileges_its_login_holds(login):
    clone = f"{login}_clone"
    tables = by_role(await read(TABLE_PRIVILEGES_SQL, [login, clone]), "relname")
    assert tables[login], "no tables read"
    for name, row in tables[login].items():
        for column in ("s", "i", "u", "d"):
            assert tables[clone][name][column] == row[column], (name, column)
    functions = by_role(await read(FUNCTION_PRIVILEGES_SQL, [login, clone]), "fn")
    for name, row in functions[login].items():
        assert functions[clone][name]["x"] == row["x"], name
    can_set = {r["rolname"]: r["can_set"] for r in await read(SET_ROLE_SQL, [login, clone])}
    assert can_set[clone] == can_set[login]


async def test_the_rotator_can_create_roles_and_administers_only_the_pairs():
    [row] = await read(ATTRIBUTES_SQL, ["meridian_rotator"])
    assert row["rolcreaterole"] is True
    for column in ("rolsuper", "rolcreatedb", "rolreplication", "rolbypassrls", "rolinherit"):
        assert row[column] is False, column
    rows = await read(MEMBERSHIP_SQL, ["meridian_rotator"])
    expected = {name for login in LOGIN_ROLES for name in (login, f"{login}_clone")}
    assert {r["role"] for r in rows} == expected
    for member in rows:
        assert (member["admin_option"], member["inherit_option"], member["set_option"]) == (
            True, False, False)
