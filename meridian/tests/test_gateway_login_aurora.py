"""meridian_gateway against the live cluster: the Lambdas work as it and nothing more is open."""

import importlib.util
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import boto3
import pytest

from backend.agentcore.identity import get_agentcore_identity
from backend.db.rds_data_client import RDSDataClient, get_rds_data_client

pytestmark = pytest.mark.database

JORDAN = "trv_meridian_demo"
DECOY = "trv_demo_decoy"
TARGETS = (
    Path(__file__).resolve().parents[1] / "meridian_agentcore" / "agentcore" / "gateway_targets"
)
HOLDS = TARGETS / "meridian_holds" / "lambda_function.py"
ZERO_VECTOR = "[" + ",".join(["0.01"] * 1024) + "]"


@pytest.fixture
def login() -> RDSDataClient:
    secret = os.environ.get("AURORA_GATEWAY_SECRET_ARN")
    if not secret:
        pytest.fail(
            "AURORA_GATEWAY_SECRET_ARN is not set: run scripts/provision_service_logins.py")
    return RDSDataClient(secret_arn=secret)


@pytest.fixture
def holds(monkeypatch):
    """The MeridianHolds Lambda's own module, wired to the gateway login instead of the master."""
    spec = importlib.util.spec_from_file_location("holds_as_gateway_login", HOLDS)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    caller = get_agentcore_identity().authorization_context()
    monkeypatch.setattr(module, "CONFIG", module.AuroraConfig(
        os.environ["AURORA_CLUSTER_ARN"], os.environ["AURORA_GATEWAY_SECRET_ARN"],
        os.getenv("AURORA_DATABASE", "meridian")))
    monkeypatch.setattr(module, "SUBJECT", (caller.provider, caller.subject_id, caller.principal))
    monkeypatch.setattr(module, "RDS", boto3.client("rds-data", region_name="us-east-1"))
    return module


async def _package_with_room() -> tuple[str, str, int]:
    rows = await get_rds_data_client().execute(
        "SELECT package_id, ROUND(price_per_person * 100)::BIGINT AS cents, availability "
        "FROM trip_packages ORDER BY package_id")
    for row in rows:
        for duration, seats in (row["availability"] or {}).items():
            if int(seats) >= 2:
                return row["package_id"], duration, int(row["cents"])
    pytest.skip("no package in the catalog has two places on any duration")


def _hold_args(package_id, duration, cents, traveler, journey_ref):
    return {
        "travelerId": traveler, "packageId": package_id, "duration": duration, "travelers": 1,
        "unitPriceCents": cents, "totalCents": cents, "holdMinutes": 15,
        "travelerConfirmed": True, "budgetCeilingCents": cents * 10, "journeyRef": journey_ref,
    }


async def _remove(booking_id, journey_ref):
    master = get_rds_data_client()
    journey = await master.execute(
        "SELECT journey_id FROM journey_threads WHERE thread_id = %s", (journey_ref,))
    await master.execute("DELETE FROM hold_requests WHERE booking_id = %s", (booking_id,))
    await master.execute("DELETE FROM booking_lines WHERE booking_id = %s", (booking_id,))
    await master.execute("DELETE FROM bookings WHERE booking_id = %s", (booking_id,))
    await master.execute("UPDATE journeys SET active_thread_id = NULL WHERE active_thread_id = %s",
                         (journey_ref,))
    await master.execute("DELETE FROM journey_threads WHERE thread_id = %s", (journey_ref,))
    for row in journey:
        await master.execute("DELETE FROM journeys WHERE journey_id = %s", (row["journey_id"],))


async def test_the_holds_lambda_places_and_confirms_a_hold_as_the_least_privilege_login(holds):
    package_id, duration, cents = await _package_with_room()
    details = holds.get_package_details({"packageId": package_id})
    assert details["package"]["package_id"] == package_id
    journey_ref = f"gwlogin-{uuid.uuid4().hex[:10]}"
    booking_id = ""
    try:
        args = _hold_args(package_id, duration, cents, JORDAN, journey_ref)
        held = holds.create_courtesy_hold(args)
        assert "error" not in held, held
        booking_id = held["hold"]["bookingId"]
        assert held["hold"]["status"] == "held"
        assert held["governance"]["decision"] == "allow"
        confirmed = holds.confirm_booking({
            "travelerId": JORDAN, "bookingId": booking_id, "totalCents": cents,
            "travelerConfirmed": True, "budgetCeilingCents": cents * 10, "journeyRef": journey_ref})
        assert confirmed["booking"]["status"] == "confirmed", confirmed
        assert Decimal(confirmed["booking"]["totalAmount"]) == Decimal(cents) / 100
    finally:
        await _remove(booking_id, journey_ref)


async def test_a_traveler_the_role_is_not_bound_to_is_refused_and_recorded(holds):
    package_id, duration, cents = await _package_with_room()
    master = get_rds_data_client()
    before = (await master.execute(
        "SELECT COUNT(*) AS n FROM traveler_access_audit WHERE requested_traveler_id = %s "
        "AND decision = 'deny'", (DECOY,)))[0]["n"]
    result = holds.create_courtesy_hold(
        _hold_args(package_id, duration, cents, DECOY, f"gwlogin-{uuid.uuid4().hex[:10]}"))
    assert result["error"] == "traveler_not_authorized"
    after = (await master.execute(
        "SELECT COUNT(*) AS n FROM traveler_access_audit WHERE requested_traveler_id = %s "
        "AND decision = 'deny'", (DECOY,)))[0]["n"]
    assert after > before, "the Lambda's deny row is written by the login, outside any transaction"


async def test_semantic_trip_search_runs_as_the_login(login):
    rows = await login.execute(
        "SELECT package_id, similarity FROM semantic_trip_search(CAST(%s AS vector), 3)",
        (ZERO_VECTOR,))
    assert len(rows) <= 3


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM bookings",
    "SELECT COUNT(*) FROM journeys",
    "SELECT COUNT(*) FROM traveler_access_audit",
    "SELECT backend_admin_count('rls_conversations')",
    "UPDATE trip_packages SET name = name WHERE false",
    "INSERT INTO traveler_identity_bindings (binding_id, identity_provider, subject_id, "
    "traveler_id) VALUES ('x', 'cognito', 'forged', 'trv_meridian_demo')",
])
async def test_the_login_cannot_read_the_travelers_or_mint_a_grant(login, sql):
    with pytest.raises(Exception, match="permission denied"):
        await login.execute(sql)


@pytest.mark.parametrize("role", ["meridian_workflow", "meridian_backend", "meridian_admin"])
async def test_the_login_cannot_become_another_role(login, role):
    with pytest.raises(Exception, match="permission denied"):
        await login.execute(f"SET ROLE {role}")
