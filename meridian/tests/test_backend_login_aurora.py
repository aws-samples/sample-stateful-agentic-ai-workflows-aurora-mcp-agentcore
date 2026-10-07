"""meridian_backend against the live cluster: the backend works as it, and nothing more is open."""

import json
import os
import subprocess
import sys

import pytest

from backend.agentcore.identity import get_agentcore_identity
from backend.authorization import TravelerAuthorizationError
from backend.db.rds_data_client import RDSDataClient, get_rds_data_client

pytestmark = pytest.mark.database

JORDAN = "trv_meridian_demo"
DECOY = "trv_demo_decoy"
PROBE_TABLES = ("traveler_preferences", "trip_interactions")


@pytest.fixture
def login() -> RDSDataClient:
    secret = os.environ.get("AURORA_BACKEND_SECRET_ARN")
    if not secret:
        pytest.fail(
            "AURORA_BACKEND_SECRET_ARN is not set: run scripts/provision_service_logins.py")
    return RDSDataClient(secret_arn=secret)


def _run_routes() -> dict:
    done = subprocess.run(
        [sys.executable, "-m", "tests.backend_login_process"],
        capture_output=True, text=True, check=False, env=dict(os.environ),
    )
    lines = [line for line in done.stdout.splitlines() if line.startswith("RESULT ")]
    assert lines, f"the backend process printed no result: {done.stderr[-600:]}"
    return json.loads(lines[-1][len("RESULT "):])


def test_the_backends_own_routes_work_as_the_least_privilege_login():
    result = _run_routes()
    assert result["current_user"] == "meridian_backend"
    statuses = result["statuses"]
    assert statuses.pop(f"/api/memory/{DECOY}") == 403
    assert set(statuses.values()) == {200}, statuses


async def test_the_probe_baseline_through_the_definer_function_equals_the_masters_count():
    result = _run_routes()
    master = get_rds_data_client()
    for table, scoped, unscoped, error in result["probe"]:
        assert error is None
        assert table in PROBE_TABLES
        total = (await master.execute(f"SELECT COUNT(*) AS n FROM {table}"))[0]["n"]
        assert unscoped == total
        assert scoped <= unscoped
    assert any(scoped < unscoped for _, scoped, unscoped, _ in result["probe"]), \
        "the decoy's rows must make the scoped count smaller than the baseline"
    assert result["effective_role"] == "meridian_app"


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM bookings",
    "SELECT COUNT(*) FROM traveler_preferences",
    "SELECT COUNT(*) FROM traveler_access_audit",
    "SELECT COUNT(*) FROM workflow_snapshots",
    "SELECT COUNT(*) FROM journeys",
    "UPDATE trip_packages SET name = name WHERE false",
    "DELETE FROM traveler_identity_bindings WHERE false",
    "INSERT INTO traveler_identity_bindings (binding_id, identity_provider, subject_id, "
    "traveler_id) VALUES ('x', 'cognito', 'forged', 'trv_meridian_demo')",
])
async def test_the_login_cannot_read_travelers_or_mint_a_grant(login, sql):
    with pytest.raises(Exception, match="permission denied"):
        await login.execute(sql)


@pytest.mark.parametrize("role", ["meridian_workflow", "meridian_gateway", "meridian_admin"])
async def test_the_login_cannot_become_another_role(login, role):
    with pytest.raises(Exception, match="permission denied"):
        await login.execute(f"SET ROLE {role}")


async def test_the_definer_function_answers_only_what_it_names(login):
    counted = await login.execute("SELECT backend_admin_count('rls_conversations') AS n")
    assert counted[0]["n"] >= 0
    with pytest.raises(Exception, match="unknown backend_admin_count kind"):
        await login.execute("SELECT backend_admin_count('bookings')")
    with pytest.raises(Exception, match="needs its window or key"):
        await login.execute("SELECT backend_admin_count('audit_deny')")


async def test_a_scope_the_workload_is_not_bound_to_is_refused_and_recorded(login):
    master = get_rds_data_client()
    before = (await master.execute(
        "SELECT COUNT(*) AS n FROM traveler_access_audit "
        "WHERE requested_traveler_id = %s AND decision = 'deny'", (DECOY,)))[0]["n"]
    with pytest.raises(TravelerAuthorizationError):
        async with login.scoped_session(
            traveler_id=DECOY, agent_type="memory_agent",
            authorization=get_agentcore_identity().authorization_context(),
        ):
            pytest.fail("the decoy scope must not open for this workload")
    after = (await master.execute(
        "SELECT COUNT(*) AS n FROM traveler_access_audit "
        "WHERE requested_traveler_id = %s AND decision = 'deny'", (DECOY,)))[0]["n"]
    assert after == before + 1, "the deny decision is evidence and must be recorded by the login"


async def test_inside_a_scope_the_login_sees_only_that_travelers_rows(login):
    async with login.scoped_session(
        traveler_id=JORDAN, agent_type="memory_agent",
        authorization=get_agentcore_identity().authorization_context(),
    ) as tx:
        rows = await login.execute(
            "SELECT DISTINCT traveler_id FROM traveler_preferences", transaction_id=tx)
        role = await login.execute("SELECT current_user AS u", transaction_id=tx)
    assert [r["traveler_id"] for r in rows] == [JORDAN]
    assert role[0]["u"] == "meridian_app"
