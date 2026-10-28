"""release_hold against the live cluster, as the least-privilege gateway login."""

import uuid

import pytest

from backend.db.journey_store import claim_execution
from backend.db.rds_data_client import get_rds_data_client
from tests import test_gateway_login_aurora as login
from tests.test_gateway_login_aurora import (
    DECOY,
    JORDAN,
    _hold_args,
    _package_with_room,
    _remove,
    _saved_cap_cents,
)

holds = login.holds

pytestmark = pytest.mark.database


async def _places(package_id, duration):
    rows = await get_rds_data_client().execute(
        "SELECT package_places_left(%s) AS availability", (package_id,))
    return int(rows[0]["availability"][duration])


async def _deny_rows(audit_id=None, traveler=None):
    column, value = ("audit_id", audit_id) if audit_id else ("requested_traveler_id", traveler)
    rows = await get_rds_data_client().execute(
        f"SELECT decision FROM traveler_access_audit WHERE {column} = %s "
        "AND decision = 'deny'", (value,))
    return len(rows)


async def _held(holds, journey_ref, request_id):
    package_id, duration, cents = await _package_with_room(
        within_cents=await _saved_cap_cents(JORDAN))
    before = await _places(package_id, duration)
    args = {**_hold_args(package_id, duration, cents, JORDAN, journey_ref),
            "holdRequestId": request_id}
    held = holds.create_courtesy_hold(args)
    assert "error" not in held, held
    return package_id, duration, before, held["hold"], cents


async def test_release_hold_returns_the_places_and_leaves_a_released_row(holds):
    journey_ref, booking_id = f"relhold-{uuid.uuid4().hex[:10]}", ""
    request_id = f"hrq_{uuid.uuid4().hex[:12]}"
    try:
        _, _, _, hold, _ = await _held(holds, journey_ref, request_id)
        booking_id = hold["bookingId"]
        arguments = {"travelerId": JORDAN, "holdRequestId": request_id,
                     "journeyId": hold["journeyId"]}
        released = holds.release_hold(arguments)
        assert released["release"]["status"] == "released", released
        assert released["release"]["replayed"] is False
        rows = await get_rds_data_client().execute(
            "SELECT status FROM bookings WHERE booking_id = %s", (booking_id,))
        assert rows == [{"status": "released"}]
        assert holds.release_hold(arguments)["release"]["replayed"] is True
    finally:
        await _remove(booking_id, journey_ref)


async def test_release_hold_restores_the_places_the_hold_took(holds):
    journey_ref = f"relhold-{uuid.uuid4().hex[:10]}"
    request_id, booking_id = f"hrq_{uuid.uuid4().hex[:12]}", ""
    try:
        package_id, duration, before, hold, _ = await _held(holds, journey_ref, request_id)
        booking_id = hold["bookingId"]
        assert await _places(package_id, duration) == before - 1, "the hold took its place"
        holds.release_hold({"travelerId": JORDAN, "holdRequestId": request_id,
                            "journeyId": hold["journeyId"]})
        assert await _places(package_id, duration) == before, "the release gave it back"
    finally:
        await _remove(booking_id, journey_ref)


async def test_release_hold_cannot_reach_another_travelers_hold(holds):
    master = get_rds_data_client()
    suffix = uuid.uuid4().hex[:10]
    journey_id, booking_id = f"jrn_rel{suffix}", f"HLD-REL{suffix.upper()}"
    request_id = f"hrq_rel{suffix}"
    denies_before = await _deny_rows(traveler=JORDAN)
    try:
        await master.execute(
            "INSERT INTO journeys (journey_id, traveler_id, checkpoint_backend) "
            "VALUES (%s, %s, 'gateway')", (journey_id, DECOY))
        await master.execute(
            "INSERT INTO bookings (booking_id, traveler_id, status, total_amount, hold_expires_at) "
            "VALUES (%s, %s, 'held', 100, CURRENT_TIMESTAMP + interval '15 minutes')",
            (booking_id, DECOY))
        await master.execute(
            "INSERT INTO hold_requests (journey_id, hold_request_id, booking_id, fingerprint, "
            "thread_id) VALUES (%s, %s, %s, 'test', %s)",
            (journey_id, request_id, booking_id, f"relhold-{suffix}"))
        result = holds.release_hold(
            {"travelerId": JORDAN, "holdRequestId": request_id, "journeyId": journey_id})
        assert result == {"error": "hold_not_found"}
        assert await _deny_rows(traveler=JORDAN) == denies_before, "Jordan holds the grant"
        rows = await master.execute(
            "SELECT status FROM bookings WHERE booking_id = %s", (booking_id,))
        assert rows == [{"status": "held"}]
    finally:
        await master.execute("DELETE FROM hold_requests WHERE booking_id = %s", (booking_id,))
        await master.execute("DELETE FROM bookings WHERE booking_id = %s", (booking_id,))
        await master.execute("DELETE FROM journeys WHERE journey_id = %s", (journey_id,))


async def test_release_hold_leaves_a_confirmed_booking_alone(holds):
    journey_ref = f"relhold-{uuid.uuid4().hex[:10]}"
    request_id, booking_id = f"hrq_{uuid.uuid4().hex[:12]}", ""
    try:
        _, _, _, hold, cents = await _held(holds, journey_ref, request_id)
        booking_id = hold["bookingId"]
        confirmed = holds.confirm_booking({
            "travelerId": JORDAN, "bookingId": booking_id, "totalCents": cents,
            "travelerConfirmed": True, "budgetCeilingCents": cents * 10,
            "journeyRef": journey_ref})
        assert confirmed["booking"]["status"] == "confirmed", confirmed
        result = holds.release_hold({"travelerId": JORDAN, "holdRequestId": request_id,
                                     "journeyId": hold["journeyId"]})
        assert result == {"error": "booking_not_held"}
        rows = await get_rds_data_client().execute(
            "SELECT status FROM bookings WHERE booking_id = %s", (booking_id,))
        assert rows == [{"status": "confirmed"}]
    finally:
        await _remove(booking_id, journey_ref)


async def test_release_hold_refuses_a_traveler_the_role_is_not_bound_to(holds):
    result = holds.release_hold(
        {"travelerId": DECOY, "holdRequestId": "hrq_none", "journeyId": "jrn_none"})
    assert result["error"] == "traveler_not_authorized"
    audit_id = result["governance"]["access_audit_id"]
    assert await _deny_rows(audit_id=audit_id) == 1, "the deny row was kept"


async def _released_with_lease(holds, expire):
    """Place a hold, claim an execution on its thread, release with that execution id."""
    master = get_rds_data_client()
    journey_ref = f"relhold-{uuid.uuid4().hex[:10]}"
    request_id, booking_id = f"hrq_{uuid.uuid4().hex[:12]}", ""
    try:
        _, _, _, hold, _ = await _held(holds, journey_ref, request_id)
        booking_id = hold["bookingId"]
        claim = await claim_execution(master, hold["journeyId"], journey_ref, "worker-release", 30)
        assert claim.claimed
        if expire:
            await master.execute(
                "UPDATE journey_executions SET lease_expires_at = CURRENT_TIMESTAMP - "
                "interval '1 minute' WHERE execution_id = %s", (claim.execution_id,))
        result = holds.release_hold({
            "travelerId": JORDAN, "holdRequestId": request_id, "journeyId": hold["journeyId"],
            "executionId": claim.execution_id})
        rows = await master.execute(
            "SELECT status FROM bookings WHERE booking_id = %s", (booking_id,))
        return result, rows
    finally:
        await master.execute("DELETE FROM journey_executions WHERE thread_id = %s", (journey_ref,))
        await _remove(booking_id, journey_ref)


async def test_release_hold_with_a_live_lease_releases(holds):
    result, rows = await _released_with_lease(holds, expire=False)
    assert result["release"]["status"] == "released", result
    assert rows == [{"status": "released"}]


async def test_release_hold_with_a_lapsed_lease_leaves_the_hold_held(holds):
    result, rows = await _released_with_lease(holds, expire=True)
    assert result == {"error": "execution_lease_lost"}
    assert rows == [{"status": "held"}]


async def test_release_hold_by_a_worker_that_was_taken_over_leaves_the_hold_held(holds):
    master = get_rds_data_client()
    journey_ref = f"relhold-{uuid.uuid4().hex[:10]}"
    request_id, booking_id = f"hrq_{uuid.uuid4().hex[:12]}", ""
    try:
        _, _, _, hold, _ = await _held(holds, journey_ref, request_id)
        booking_id = hold["bookingId"]
        first = await claim_execution(master, hold["journeyId"], journey_ref, "worker-a", 30)
        assert first.claimed
        await master.execute(
            "UPDATE journey_executions SET lease_expires_at = CURRENT_TIMESTAMP - "
            "interval '1 minute' WHERE execution_id = %s", (first.execution_id,))
        second = await claim_execution(master, hold["journeyId"], journey_ref, "worker-b", 30)
        assert second.claimed and second.execution_id != first.execution_id
        result = holds.release_hold({
            "travelerId": JORDAN, "holdRequestId": request_id, "journeyId": hold["journeyId"],
            "executionId": first.execution_id})
        assert result == {"error": "execution_lease_lost"}
        rows = await master.execute(
            "SELECT status FROM bookings WHERE booking_id = %s", (booking_id,))
        assert rows == [{"status": "held"}]
    finally:
        await master.execute("DELETE FROM journey_executions WHERE thread_id = %s", (journey_ref,))
        await _remove(booking_id, journey_ref)
