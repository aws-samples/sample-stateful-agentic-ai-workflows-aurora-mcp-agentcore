"""Migration 022 gives the MeridianCatalog Lambda its own login, which can only read the catalog."""

import pathlib

import pytest

from scripts.init_aurora_schema import split_sql

MIGRATION = (
    pathlib.Path(__file__).parent.parent / "scripts" / "migrations" / "022_catalog_login.sql"
)
OUT_OF_REACH = (
    "traveler_identity_bindings", "traveler_access_audit", "bookings", "booking_lines",
)


@pytest.fixture
def statements():
    code = "\n".join(
        line for line in MIGRATION.read_text().splitlines() if not line.startswith("--")
    )
    return [" ".join(s.split()).lower().rstrip(";") for s in split_sql(code)]


def test_the_login_is_created_without_login_or_bypass(statements):
    create = next(s for s in statements if s.startswith("do $$") and "create role" in s)
    assert ("create role meridian_catalog nologin nobypassrls noinherit nocreatedb nocreaterole"
            in create)
    assert "if not exists (select from pg_roles where rolname = 'meridian_catalog')" in create


def test_a_tampered_existing_role_stops_the_migration(statements):
    create = next(s for s in statements if s.startswith("do $$") and "create role" in s)
    assert "elsif exists (" in create
    assert "rolsuper or rolbypassrls or rolinherit or rolcreatedb or rolcreaterole" in create
    assert "or rolreplication" in create
    assert "raise exception 'meridian_catalog exists with attributes 022 does not allow" in create


def test_a_pre_existing_role_with_memberships_or_extra_grants_stops_the_migration(statements):
    create = next(s for s in statements if s.startswith("do $$") and "create role" in s)
    assert "from pg_auth_members m join pg_roles r on r.oid = m.member" in create
    assert "where r.rolname = 'meridian_catalog'" in create
    assert "aclexplode(c.relacl)" in create
    assert "not (c.relname = 'trip_packages' and a.privilege_type = 'select')" in create
    assert ("raise exception 'meridian_catalog exists with memberships or table grants "
            "022 does not allow") in create


def test_the_role_is_never_altered_into_shape_and_no_password_is_in_the_file(statements):
    assert not any(s.startswith("alter role") for s in statements)
    assert "password" not in MIGRATION.read_text().lower()


def test_the_login_holds_one_table_read_and_one_function_and_nothing_else(statements):
    grants = [s for s in statements if s.startswith("grant")]
    assert grants == [
        "grant select on trip_packages to meridian_catalog",
        "grant execute on function package_places_left(text) to meridian_catalog",
    ]


def test_the_login_reaches_no_other_role(statements):
    assert not any(s.startswith("grant meridian") for s in statements)
    assert not any("meridian_app" in s for s in statements)
    assert not any("set role" in s for s in statements)


@pytest.mark.parametrize("table", OUT_OF_REACH)
def test_the_login_gets_nothing_on_traveler_or_booking_tables(statements, table):
    assert not any(table in s for s in statements)


def test_the_migration_creates_no_policy_and_no_function(statements):
    assert not any(s.startswith(("create policy", "create function", "create or replace",
                                 "revoke", "drop")) for s in statements)
