"""package_places_left against the live cluster: capacity minus every traveler's live reservations.

The rows are written as the master login, which RLS does not bind, on one real catalog package.
Each test removes its own bookings.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

from backend.db.rds_data_client import get_rds_data_client

pytestmark = pytest.mark.database

JORDAN = "trv_meridian_demo"
DECOY = "trv_demo_decoy"
PLACES_LEFT_SQL = "SELECT package_places_left(%s) AS places"
CALLERS = ("meridian_app", "meridian_backend", "meridian_gateway", "meridian_workflow",
           "meridian_catalog")


def _later(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


@pytest_asyncio.fixture
async def booked():
    client = get_rds_data_client()
    made: list[str] = []

    async def book(traveler, package_id, duration, places, status, expires_at=None):
        booking_id = f"HLD-ITEST{uuid.uuid4().hex[:6].upper()}"
        await client.execute(
            "INSERT INTO bookings (booking_id, traveler_id, status, total_amount, hold_expires_at) "
            "VALUES (%s, %s, %s, 1, %s::TIMESTAMPTZ)",
            (booking_id, traveler, status, expires_at),
        )
        made.append(booking_id)
        await client.execute(
            "INSERT INTO booking_lines (booking_id, package_id, duration, travelers_count, "
            "unit_price) VALUES (%s, %s, %s, %s, 1)",
            (booking_id, package_id, duration, places),
        )
        return booking_id

    yield client, book
    for booking_id in made:
        await client.execute("DELETE FROM booking_lines WHERE booking_id = %s", (booking_id,))
        await client.execute("DELETE FROM bookings WHERE booking_id = %s", (booking_id,))


async def _places_left(client, package_id):
    rows = await client.execute(PLACES_LEFT_SQL, (package_id,))
    places = rows[0]["places"]
    return json.loads(places) if isinstance(places, str) else places


async def _a_package_with_two_durations(client):
    rows = await client.execute(
        "SELECT package_id, availability FROM trip_packages ORDER BY package_id", ()
    )
    for row in rows:
        availability = row["availability"]
        availability = json.loads(availability) if isinstance(availability, str) else availability
        counted = {d: int(n) for d, n in (availability or {}).items() if isinstance(n, int)}
        roomy = [d for d, n in counted.items() if n >= 4]
        if roomy and len(counted) >= 2:
            left = await _places_left(client, row["package_id"])
            if left[roomy[0]] >= 4:
                other = next(d for d in counted if d != roomy[0])
                return row["package_id"], roomy[0], other, left
    pytest.skip("no catalog package has two durations and four free places on one")


async def test_live_reservations_of_every_traveler_reduce_places_left(booked):
    client, book = booked
    package_id, duration, other, before = await _a_package_with_two_durations(client)
    await book(DECOY, package_id, duration, 1, "confirmed")
    await book(JORDAN, package_id, duration, 2, "held", _later(1))

    after = await _places_left(client, package_id)
    assert after[duration] == before[duration] - 3
    assert after[other] == before[other], "another duration's places are untouched"


async def test_released_and_lapsed_holds_count_nothing(booked):
    client, book = booked
    package_id, duration, _other, before = await _a_package_with_two_durations(client)
    await book(DECOY, package_id, duration, 1, "released", _later(1))
    await book(JORDAN, package_id, duration, 1, "held", _later(-1))
    await book(JORDAN, package_id, duration, 1, "expired", _later(-1))

    assert (await _places_left(client, package_id))[duration] == before[duration]


async def test_a_fully_reserved_duration_reads_zero_and_never_negative(booked):
    client, book = booked
    package_id, duration, _other, before = await _a_package_with_two_durations(client)
    await book(DECOY, package_id, duration, before[duration], "held", _later(1))
    assert (await _places_left(client, package_id))[duration] == 0

    await book(JORDAN, package_id, duration, 2, "confirmed")
    assert (await _places_left(client, package_id))[duration] == 0


async def test_an_unknown_package_reads_as_no_row():
    client = get_rds_data_client()
    rows = await client.execute(PLACES_LEFT_SQL, (f"NOPE-{uuid.uuid4().hex[:6]}",))
    assert rows == [{"places": None}]


async def test_only_the_service_roles_may_call_it():
    client = get_rds_data_client()
    rows = await client.execute(
        "SELECT r.rolname AS role, has_function_privilege(r.rolname, "
        "'package_places_left(text)', 'EXECUTE') AS allowed FROM pg_roles r "
        "WHERE r.rolname LIKE 'meridian%%' ORDER BY 1",
        (),
    )
    allowed = {row["role"] for row in rows if row["allowed"]}
    assert set(CALLERS) <= allowed
    assert allowed - set(CALLERS) <= {"meridian_admin"}, "the owner, and nobody else"
    public = await client.execute(
        "SELECT bool_or(a.grantee = 0) AS public_execute FROM pg_proc p, "
        "aclexplode(p.proacl) a WHERE p.proname = 'package_places_left'",
        (),
    )
    assert public == [{"public_execute": False}]
