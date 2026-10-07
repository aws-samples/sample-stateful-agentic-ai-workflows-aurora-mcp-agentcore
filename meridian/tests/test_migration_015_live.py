"""Migration 015 as applied: what meridian_workflow can and cannot do."""

import pytest

from backend.db.rds_data_client import get_rds_data_client

pytestmark = pytest.mark.database

TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
SEQUENCE_PRIVILEGES = ("USAGE", "SELECT", "UPDATE")


async def _rows(sql, params=()):
    return await get_rds_data_client().execute(sql, params)


async def _one(sql, params=()):
    return (await _rows(sql, params))[0]


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


async def test_the_login_has_one_membership_with_set_only():
    rows = await _rows(
        "SELECT g.rolname AS granted, m.admin_option, m.inherit_option, m.set_option "
        "FROM pg_auth_members m "
        "JOIN pg_roles g ON g.oid = m.roleid "
        "JOIN pg_roles u ON u.oid = m.member "
        "WHERE u.rolname = 'meridian_workflow'"
    )
    assert rows == [
        {
            "granted": "meridian_app",
            "admin_option": False,
            "inherit_option": False,
            "set_option": True,
        }
    ]


async def test_the_login_holds_exactly_its_table_privileges():
    rows = await _rows(
        "SELECT t.table_name AS name, p.priv AS priv "
        "FROM information_schema.tables t "
        "CROSS JOIN unnest(string_to_array(%s, ',')) AS p(priv) "
        "WHERE t.table_schema = 'public' "
        "AND has_table_privilege('meridian_workflow', 'public.' || t.table_name, p.priv)",
        (",".join(TABLE_PRIVILEGES),),
    )
    assert {(r["name"], r["priv"]) for r in rows} == {
        ("traveler_identity_bindings", "SELECT"),
        ("traveler_access_audit", "INSERT"),
        ("trip_packages", "SELECT"),
        ("journeys", "SELECT"),
        ("journey_threads", "SELECT"),
        ("journey_executions", "SELECT"),
        ("workflow_snapshots", "SELECT"),
        ("workflow_snapshots", "INSERT"),
    }


async def test_the_login_holds_exactly_its_sequence_privileges():
    rows = await _rows(
        "SELECT s.sequencename AS name, p.priv AS priv "
        "FROM pg_sequences s "
        "CROSS JOIN unnest(string_to_array(%s, ',')) AS p(priv) "
        "WHERE s.schemaname = 'public' "
        "AND has_sequence_privilege('meridian_workflow', 'public.' || s.sequencename, p.priv)",
        (",".join(SEQUENCE_PRIVILEGES),),
    )
    assert {(r["name"], r["priv"]) for r in rows} == {
        ("workflow_snapshots_snapshot_seq_seq", "USAGE")
    }


async def _policy(name):
    return await _one(
        "SELECT tablename, cmd, roles::text AS roles, qual, with_check "
        "FROM pg_policies WHERE schemaname = 'public' AND policyname = %s",
        (name,),
    )


@pytest.mark.parametrize(
    ("name", "cmd", "expression"),
    [
        ("workflow_snapshots_workflow_select", "SELECT", "qual"),
        ("workflow_snapshots_workflow_insert", "INSERT", "with_check"),
    ],
)
async def test_the_snapshot_policies_pin_the_traveler_and_a_bound_thread(name, cmd, expression):
    row = await _policy(name)
    assert row["tablename"] == "workflow_snapshots"
    assert row["cmd"] == cmd
    assert row["roles"] == "{meridian_workflow}"
    assert "current_setting('app.current_traveler_id'" in row[expression]
    assert "journey_threads" in row[expression]


async def test_the_audit_insert_policy_is_for_the_login_only():
    row = await _policy("traveler_access_audit_workflow_insert")
    assert row["tablename"] == "traveler_access_audit"
    assert row["cmd"] == "INSERT"
    assert row["roles"] == "{meridian_workflow}"
    assert row["with_check"] == "true"


async def test_session_stops_exist_with_rls_and_a_traveler_policy():
    row = await _one(
        "SELECT relrowsecurity FROM pg_class WHERE oid = 'public.workflow_session_stops'::regclass"
    )
    assert row["relrowsecurity"] is True
    policy = await _policy("workflow_session_stops_traveler_scope")
    assert policy["tablename"] == "workflow_session_stops"
    assert policy["roles"] == "{public}"
