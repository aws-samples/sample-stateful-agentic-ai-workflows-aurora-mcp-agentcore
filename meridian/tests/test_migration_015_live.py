"""Migration 015 as applied: what meridian_workflow can and cannot do."""

import pytest

from backend.db.rds_data_client import get_rds_data_client

pytestmark = pytest.mark.database


async def _one(sql, params=()):
    return (await get_rds_data_client().execute(sql, params))[0]


async def test_the_login_cannot_bypass_rls_or_own_anything():
    row = await _one(
        "SELECT rolbypassrls, rolinherit, rolcreatedb, rolcreaterole, rolsuper "
        "FROM pg_roles WHERE rolname = 'meridian_workflow'"
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
        "WHERE r.rolname = 'meridian_workflow'"
    )
    assert owned["n"] == 0


async def test_the_login_reaches_the_app_role_by_set_role_only():
    row = await _one(
        "SELECT pg_has_role('meridian_workflow', 'meridian_app', 'SET') AS can_set, "
        "pg_has_role('meridian_workflow', 'meridian_app', 'USAGE') AS inherits"
    )
    assert row == {"can_set": True, "inherits": False}


@pytest.mark.parametrize(
    ("table", "privilege", "expected"),
    [
        ("workflow_snapshots", "INSERT", True),
        ("workflow_snapshots", "UPDATE", False),
        ("workflow_snapshots", "DELETE", False),
        ("journey_executions", "SELECT", True),
        ("journey_executions", "UPDATE", False),
        ("bookings", "SELECT", False),
        ("traveler_identity_bindings", "INSERT", False),
        ("trip_packages", "SELECT", True),
    ],
)
async def test_the_login_holds_only_its_grants(table, privilege, expected):
    row = await _one(
        "SELECT has_table_privilege('meridian_workflow', %s, %s) AS ok", (table, privilege)
    )
    assert row["ok"] is expected


async def test_session_stops_exist_with_rls():
    row = await _one("SELECT relrowsecurity FROM pg_class WHERE relname = 'workflow_session_stops'")
    assert row["relrowsecurity"] is True
