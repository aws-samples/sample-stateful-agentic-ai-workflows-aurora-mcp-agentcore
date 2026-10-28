"""Migration 022 as applied: meridian_catalog runs every catalog statement and nothing more."""

import os
import re

import boto3
import pytest

from backend.db.rds_data_client import RDSDataClient, get_rds_data_client
from tests.lambda_targets_support import load_target

pytestmark = pytest.mark.database

LOGIN = "meridian_catalog"
TABLE_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
COLUMN_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "REFERENCES")
APPLICATION_SCHEMAS = (
    "n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname !~ '^pg_'"
)
WRITING_SQL = re.compile(r"\b(insert|update|delete|truncate|merge|copy|call)\b", re.IGNORECASE)
SEQUENCE_PRIVILEGES = ("USAGE", "SELECT", "UPDATE")
ZERO_VECTOR = "[" + ",".join(["0.01"] * 1024) + "]"


def _secret() -> str:
    secret = os.environ.get("AURORA_CATALOG_SECRET_ARN")
    if not secret:
        pytest.fail("AURORA_CATALOG_SECRET_ARN is not set: run (L0b) "
                    "scripts/provision_service_logins.py --login catalog --apply "
                    "--i-understand-this-changes-aws --write-env")
    return secret


@pytest.fixture
def login() -> RDSDataClient:
    return RDSDataClient(secret_arn=_secret())


@pytest.fixture
def catalog(monkeypatch):
    """The catalog Lambda's own module, reading Amazon Aurora as meridian_catalog."""
    module = load_target("meridian_catalog", "meridian_catalog_as_its_login")
    region = os.getenv("AGENTCORE_REGION") or os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setattr(module, "CONFIG", (
        os.environ["AURORA_CLUSTER_ARN"], _secret(), os.getenv("AURORA_DATABASE", "meridian")))
    monkeypatch.setattr(module, "RDS", boto3.client("rds-data", region_name=region))
    return module


async def _master(sql, params=()):
    return await get_rds_data_client().execute(sql, tuple(params))


async def test_the_login_can_log_in_but_cannot_bypass_rls_or_own_anything():
    rows = await _master(
        "SELECT rolcanlogin, rolbypassrls, rolinherit, rolcreatedb, rolcreaterole, rolsuper "
        "FROM pg_roles WHERE rolname = %s", (LOGIN,))
    assert rows == [{"rolcanlogin": True, "rolbypassrls": False, "rolinherit": False,
                     "rolcreatedb": False, "rolcreaterole": False, "rolsuper": False}]
    owned = await _master(
        "SELECT (SELECT COUNT(*) FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
        "WHERE r.rolname = %s) + "
        "(SELECT COUNT(*) FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner "
        "WHERE r.rolname = %s) + "
        "(SELECT COUNT(*) FROM pg_type y JOIN pg_roles r ON r.oid = y.typowner "
        "WHERE r.rolname = %s) + "
        "(SELECT COUNT(*) FROM pg_namespace s JOIN pg_roles r ON r.oid = s.nspowner "
        "WHERE r.rolname = %s) AS n", (LOGIN, LOGIN, LOGIN, LOGIN))
    assert owned == [{"n": 0}]


async def test_the_login_is_a_member_of_no_role():
    rows = await _master(
        "SELECT g.rolname AS granted FROM pg_auth_members m "
        "JOIN pg_roles g ON g.oid = m.roleid JOIN pg_roles u ON u.oid = m.member "
        "WHERE u.rolname = %s", (LOGIN,))
    assert rows == []


async def test_the_login_holds_exactly_one_table_or_column_privilege():
    rows = await _master(
        "SELECT c.relname AS name, p.priv AS priv "
        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "CROSS JOIN unnest(string_to_array(%s, ',')) AS p(priv) "
        f"WHERE {APPLICATION_SCHEMAS} AND c.relkind IN ('r', 'p', 'v', 'm', 'f') "
        "AND CASE WHEN p.priv = ANY (string_to_array(%s, ',')) "
        "THEN has_any_column_privilege(%s, c.oid, p.priv) "
        "ELSE has_table_privilege(%s, c.oid, p.priv) END",
        (",".join(TABLE_PRIVILEGES), ",".join(COLUMN_PRIVILEGES), LOGIN, LOGIN))
    assert {(r["name"], r["priv"]) for r in rows} == {("trip_packages", "SELECT")}


async def test_the_login_holds_no_sequence_privilege():
    rows = await _master(
        "SELECT s.sequencename AS name, p.priv AS priv "
        "FROM pg_sequences s "
        "CROSS JOIN unnest(string_to_array(%s, ',')) AS p(priv) "
        "WHERE s.schemaname = 'public' "
        "AND has_sequence_privilege(%s, 'public.' || s.sequencename, p.priv)",
        (",".join(SEQUENCE_PRIVILEGES), LOGIN))
    assert rows == []


@pytest.mark.parametrize(("function", "allowed"), [
    ("package_places_left(text)", True),
    ("backend_admin_count(text, interval, text)", False),
])
async def test_the_login_calls_places_left_and_no_other_closed_function(function, allowed):
    rows = await _master(
        "SELECT has_function_privilege(%s, %s, 'EXECUTE') AS allowed", (LOGIN, function))
    assert rows == [{"allowed": allowed}]


async def test_the_only_definer_function_the_login_can_execute_is_places_left():
    rows = await _master(
        "SELECT p.oid::regprocedure::text AS fn FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid = p.pronamespace "
        f"WHERE {APPLICATION_SCHEMAS} AND p.prosecdef "
        "AND has_function_privilege(%s, p.oid, 'EXECUTE')", (LOGIN,))
    assert {r["fn"] for r in rows} == {"package_places_left(text)"}


async def test_the_other_functions_the_login_runs_through_public_are_read_only():
    """Accepted: pg_catalog and information_schema built-ins, whose own checks guard anything
    privileged, and functions an extension owns (pgvector's operators). Every other
    non-definer function the login can run must be semantic_trip_search, whose body only reads.
    """
    rows = await _master(
        "SELECT p.oid::regprocedure::text AS fn, p.prosrc AS src FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid = p.pronamespace "
        f"WHERE {APPLICATION_SCHEMAS} AND NOT p.prosecdef "
        "AND has_function_privilege(%s, p.oid, 'EXECUTE') "
        "AND NOT EXISTS (SELECT FROM pg_depend d WHERE d.objid = p.oid "
        "AND d.classid = 'pg_proc'::regclass AND d.deptype = 'e')", (LOGIN,))
    assert {r["fn"] for r in rows} == {"semantic_trip_search(vector,integer)"}
    assert not any(WRITING_SQL.search(r["src"]) for r in rows)


@pytest.mark.parametrize("function", ["create_courtesy_hold", "confirm_booking"])
async def test_the_login_cannot_call_the_booking_writers(login, function):
    call = (await _master(
        "SELECT p.proname || '(' || COALESCE((SELECT string_agg('NULL::' || t::regtype::text, "
        "', ' ORDER BY o) FROM unnest(p.proargtypes::oid[]) WITH ORDINALITY AS u(t, o)), '') "
        "|| ')' AS call FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND p.proname = %s", (function,)))
    assert len(call) == 1, f"expected exactly one {function} overload, found {len(call)}"
    with pytest.raises(Exception, match=f"permission denied for function {function}"):
        await login.execute(f"SELECT {call[0]['call']}")


async def test_each_hybrid_statement_runs_as_the_login(catalog):
    ids = [r["package_id"] for r in await _master(
        "SELECT package_id FROM trip_packages ORDER BY package_id LIMIT 2")]

    embedded = (await _master(
        "SELECT COUNT(*) AS n FROM trip_packages WHERE embedding IS NOT NULL"))[0]["n"]
    assert embedded >= 1
    assert len(catalog.execute(catalog.SEMANTIC_SQL, (ZERO_VECTOR, 3))) == min(3, embedded)
    word = (await _master("SELECT destination FROM trip_packages WHERE destination IS NOT NULL "
                          "ORDER BY package_id LIMIT 1"))[0]["destination"]
    assert len(catalog.execute(catalog.LEXICAL_SQL, (word, 3))) >= 1
    hydrate = catalog.HYDRATE_SQL.format(placeholders=", ".join(["%s"] * len(ids)))
    hydrated = catalog.execute(hydrate, tuple(ids))

    assert sorted(r["package_id"] for r in hydrated) == sorted(ids)
    assert all("availability" in r for r in hydrated)


async def test_each_filter_tool_runs_its_statement_as_the_login(catalog):
    sample = await _master(
        "SELECT package_id, trip_type, region, destination FROM trip_packages "
        "WHERE region IS NOT NULL AND destination IS NOT NULL ORDER BY package_id LIMIT 2")
    first = sample[0]

    found = catalog.search_trips({"trip_type": first["trip_type"], "text": first["destination"],
                                  "max_price_per_person": 100000, "limit": 3})
    assert found["packages"]
    assert all(p["trip_type"] == first["trip_type"] for p in found["packages"])
    compared = catalog.compare_packages({"package_ids": [r["package_id"] for r in sample]})
    assert sorted(p["package_id"] for p in compared) == sorted(r["package_id"] for r in sample)
    assert catalog.price_range({"destination": first["destination"]})["sample_size"] >= 1
    inventory = catalog.region_inventory({"region": first["region"],
                                          "trip_type": first["trip_type"]})
    assert inventory["package_count"] >= 1


REFUSED = [
    ("SELECT COUNT(*) FROM traveler_identity_bindings", "table traveler_identity_bindings"),
    ("INSERT INTO traveler_access_audit (audit_id, identity_provider, subject_id, "
     "requested_traveler_id, decision) SELECT 'x', 'cognito', 'forged', 'trv_meridian_demo', "
     "'deny' WHERE false", "table traveler_access_audit"),
    ("SELECT COUNT(*) FROM bookings", "table bookings"),
    ("SELECT COUNT(*) FROM booking_lines", "table booking_lines"),
    ("SELECT backend_admin_count('rls_conversations')", "function backend_admin_count"),
    ("UPDATE trip_packages SET name = name WHERE false", "table trip_packages"),
    ("DELETE FROM trip_packages WHERE false", "table trip_packages"),
    ("INSERT INTO trip_packages (package_id) SELECT 'x' WHERE false", "table trip_packages"),
]


@pytest.mark.parametrize(("sql", "obj"), REFUSED)
async def test_the_login_cannot_read_a_traveler_write_an_audit_row_or_change_the_catalog(
        login, sql, obj):
    with pytest.raises(Exception, match=f"permission denied for {obj}"):
        await login.execute(sql)


async def test_the_login_cannot_become_the_app_role(login):
    with pytest.raises(Exception, match="permission denied to set role"):
        await login.execute("SET ROLE meridian_app")
