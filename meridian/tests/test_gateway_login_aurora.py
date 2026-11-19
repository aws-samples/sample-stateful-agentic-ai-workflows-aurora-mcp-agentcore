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
from backend.agents.budget import budget_ceiling_per_traveler_cents
from backend.db.rds_data_client import RDSDataClient, get_rds_data_client
from tests.live_support import choose_package, cleanup_run, report_cleanup

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


async def _saved_cap_cents(traveler: str) -> int:
    """The traveler's saved per-person cap, read the way the backend derives it."""
    facts = await get_rds_data_client().execute(
        "SELECT preference_key AS key, preference_value AS value FROM traveler_preferences "
        "WHERE traveler_id = %s ORDER BY confidence DESC NULLS LAST, last_seen_at DESC LIMIT 50", (traveler,))
    cap = budget_ceiling_per_traveler_cents(facts)
    if cap is None:
        pytest.skip(f"{traveler} has no saved budget fact")
    return cap


async def _package_with_room(
    *, within_cents=None, above_cents=None, seats: int = 2, roomiest: bool = False
) -> tuple[str, str, int]:
    """A package and duration with room, by ``package_id`` order or, with ``roomiest``, the most.

    A test that confirms a booking passes ``roomiest=True``: its booking stays and uses places
    for good, so the cost is spread over the package with the most places left.
    """
    rows = await get_rds_data_client().execute(
        "SELECT package_id, ROUND(price_per_person * 100)::BIGINT AS cents, "
        "package_places_left(package_id) AS availability FROM trip_packages ORDER BY package_id")
    chosen = choose_package(rows, within_cents=within_cents, above_cents=above_cents,
                            seats=seats, roomiest=roomiest)
    if chosen is None:
        pytest.skip(f"no catalog package with {seats} places left fits the price bound")
    return chosen


def _hold_args(package_id, duration, cents, traveler, journey_ref):
    return {
        "travelerId": traveler, "packageId": package_id, "duration": duration, "travelers": 1,
        "unitPriceCents": cents, "totalCents": cents, "holdMinutes": 15,
        "travelerConfirmed": True, "budgetCeilingCents": cents * 10, "journeyRef": journey_ref,
    }


async def _give_back(booking_id, journey_ref):
    """Give back every hold the test placed, a lost reply's included, and delete nothing.

    The booking, hold request, journey and action audit rows stay: the audit export may already
    hold them. Every id is the test's own, so earlier runs' rows never matter. Called from a
    ``finally``, it sees the test's own failure in flight: that stays the primary error and a
    cleanup problem is added to it as a note, as in the lease test.
    """
    failure = sys.exc_info()[1]
    report_cleanup(await cleanup_run(
        get_rds_data_client(), JORDAN, test="gateway_login", threads=(journey_ref,),
        bookings=(booking_id,) if booking_id else ()), failure)


async def test_the_holds_lambda_places_and_confirms_a_hold_as_the_least_privilege_login(holds):
    """Place and confirm a hold as the gateway login.

    Residual cost: this test leaves one confirmed booking for one traveler, kept as audit
    evidence and using one package place for good. It picks the package with the most places
    left to spread that cost. Capacity comes back only through
    ``scripts/release_demo_bookings.py --delete-from-disposable-database`` on a disposable
    database, or by raising availability.
    """
    package_id, duration, cents = await _package_with_room(
        within_cents=await _saved_cap_cents(JORDAN), roomiest=True
    )
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
        await _give_back(booking_id, journey_ref)


async def test_a_hold_above_the_saved_budget_is_refused_whatever_ceiling_the_caller_sends(holds):
    """The direct-caller case: truthful price and total, an inflated budgetCeilingCents."""
    package_id, duration, cents = await _package_with_room(
        above_cents=await _saved_cap_cents(JORDAN)
    )
    journey_ref = f"gwlogin-{uuid.uuid4().hex[:10]}"
    args = {**_hold_args(package_id, duration, cents, JORDAN, journey_ref),
            "budgetCeilingCents": cents * 100}
    result = {}
    try:
        result = holds.create_courtesy_hold(args)
        assert result == {"error": "budget_ceiling_exceeded"}
        held = await get_rds_data_client().execute(
            "SELECT COUNT(*) AS n FROM hold_requests hr JOIN journey_threads jt "
            "ON jt.journey_id = hr.journey_id WHERE jt.thread_id = %s", (journey_ref,))
        assert held == [{"n": 0}]
    finally:
        await _give_back((result.get("hold") or {}).get("bookingId", ""), journey_ref)


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


@pytest.mark.parametrize("table", ["bookings", "journeys", "hold_requests"])
async def test_the_login_reads_no_booking_rows_without_a_traveler_scope(login, table):
    """Migration 028 grants SELECT for the action audit's checks; row-level security scopes it."""
    rows = await login.execute(f"SELECT COUNT(*) AS n FROM {table}")
    assert rows[0]["n"] == 0


@pytest.mark.parametrize("sql", [
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
