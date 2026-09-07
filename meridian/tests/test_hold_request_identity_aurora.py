"""create_courtesy_hold, against real Aurora.

The idempotency claim is only worth what the database enforces. These exercise
the function itself: a repeat of one request replays the hold it already made,
a repeat with different terms is refused, and a journey the caller does not own
is not a way to reach someone else's booking.

Requires migrations 007 and 008 and AWS credentials.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backend.agentcore.identity import get_agentcore_identity
from backend.agents.orchestration_05.hold_intent import (
    fingerprint_terms,
    normalize_hold_terms,
)
from backend.db.journey_store import ScopedDb, ensure_journey
from backend.db.rds_data_client import get_rds_data_client

pytestmark = pytest.mark.database

TRAVELER = "trv_meridian_demo"

HOLD_SQL = """
SELECT booking_id, status, replayed,
       seats_available, seats_reserved, seats_remaining
FROM create_courtesy_hold(
    %s::TEXT, %s::TEXT, %s::TEXT, %s::TEXT, %s::TEXT,
    %s::TEXT, %s::TEXT,
    %s::INTEGER, %s::NUMERIC, %s::NUMERIC, %s::TIMESTAMPTZ
)
"""


async def _a_package_with_room(client) -> tuple[str, str, Decimal]:
    """Find a package and duration that has at least two places published."""
    rows = await client.execute(
        """
        SELECT package_id, price_per_person, availability
          FROM trip_packages
         WHERE availability IS NOT NULL
         ORDER BY package_id
        """,
        (),
    )
    for row in rows:
        availability = row["availability"]
        if isinstance(availability, str):
            import json

            availability = json.loads(availability)
        for duration, seats in (availability or {}).items():
            if int(seats) >= 2:
                return (
                    row["package_id"],
                    duration,
                    Decimal(str(row["price_per_person"])),
                )
    pytest.skip("no package in the catalog has two places on any duration")


async def test_one_request_produces_one_hold_however_often_it_runs() -> None:
    client = get_rds_data_client()
    booking_id = f"HLD-ITEST{uuid.uuid4().hex[:6].upper()}"
    thread_id = f"itest-hold-{uuid.uuid4().hex[:8]}"
    journey_id = ""
    try:
        package_id, duration, price = await _a_package_with_room(client)
        terms = normalize_hold_terms(package_id, duration, 1, price)
        fingerprint = fingerprint_terms(terms)
        request_id = f"hrq_{booking_id}"
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        async with client.scoped_session(
            traveler_id=TRAVELER,
            agent_type="booking_agent",
            authorization=get_agentcore_identity().authorization_context(),
        ) as tx:
            journey_id = await ensure_journey(
                ScopedDb(client, tx), TRAVELER, thread_id, "AuroraDataApiSaver"
            )
            args = (
                booking_id,
                TRAVELER,
                journey_id,
                request_id,
                fingerprint,
                package_id,
                duration,
                1,
                price,
                price,
                expires,
            )

            first = (await client.execute(HOLD_SQL, args, transaction_id=tx))[0]
            assert first["replayed"] is False
            assert first["booking_id"] == booking_id
            reserved_after_first = first["seats_reserved"]

            second = (await client.execute(HOLD_SQL, args, transaction_id=tx))[0]
            assert second["replayed"] is True, "the same request must not re-reserve"
            assert second["booking_id"] == booking_id
            assert second["seats_reserved"] is None, "a replay counts no inventory"

            lines = await client.execute(
                "SELECT count(*) AS n FROM booking_lines WHERE booking_id = %s",
                (booking_id,),
                transaction_id=tx,
            )
            assert int(lines[0]["n"]) == 1, "a replay must not add a second line"
            assert reserved_after_first is not None
    finally:
        await _cleanup(client, booking_id, thread_id, journey_id)


async def test_the_same_request_id_with_different_terms_is_refused() -> None:
    """Reusing a request id for a different hold is a bug, not a replay."""
    client = get_rds_data_client()
    booking_id = f"HLD-ITEST{uuid.uuid4().hex[:6].upper()}"
    thread_id = f"itest-hold-{uuid.uuid4().hex[:8]}"
    journey_id = ""
    try:
        package_id, duration, price = await _a_package_with_room(client)
        request_id = f"hrq_{booking_id}"
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        async with client.scoped_session(
            traveler_id=TRAVELER,
            agent_type="booking_agent",
            authorization=get_agentcore_identity().authorization_context(),
        ) as tx:
            journey_id = await ensure_journey(
                ScopedDb(client, tx), TRAVELER, thread_id, "AuroraDataApiSaver"
            )

            def args(quantity: int) -> tuple:
                fingerprint = fingerprint_terms(
                    normalize_hold_terms(package_id, duration, quantity, price)
                )
                return (
                    booking_id,
                    TRAVELER,
                    journey_id,
                    request_id,
                    fingerprint,
                    package_id,
                    duration,
                    quantity,
                    price,
                    price * quantity,
                    expires,
                )

            await client.execute(HOLD_SQL, args(1), transaction_id=tx)
            with pytest.raises(Exception, match="hold_request_parameter_mismatch"):
                await client.execute(HOLD_SQL, args(2), transaction_id=tx)
    finally:
        await _cleanup(client, booking_id, thread_id, journey_id)


async def test_a_journey_the_caller_does_not_own_is_refused() -> None:
    """A request id is not a capability: the journey must belong to the caller."""
    client = get_rds_data_client()
    booking_id = f"HLD-ITEST{uuid.uuid4().hex[:6].upper()}"
    try:
        package_id, duration, price = await _a_package_with_room(client)
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        async with client.scoped_session(
            traveler_id=TRAVELER,
            agent_type="booking_agent",
            authorization=get_agentcore_identity().authorization_context(),
        ) as tx:
            with pytest.raises(Exception, match="journey_not_owned"):
                await client.execute(
                    HOLD_SQL,
                    (
                        booking_id,
                        TRAVELER,
                        "jrn_not_yours",
                        f"hrq_{booking_id}",
                        "fingerprint",
                        package_id,
                        duration,
                        1,
                        price,
                        price,
                        expires,
                    ),
                    transaction_id=tx,
                )
    finally:
        await _cleanup(client, booking_id, "", "")


async def _cleanup(client, booking_id: str, thread_id: str, journey_id: str) -> None:
    """Remove everything the test wrote, as the admin role."""
    await client.execute(
        "DELETE FROM hold_requests WHERE booking_id = %s", (booking_id,)
    )
    await client.execute(
        "DELETE FROM booking_lines WHERE booking_id = %s", (booking_id,)
    )
    await client.execute("DELETE FROM bookings WHERE booking_id = %s", (booking_id,))
    if thread_id:
        await client.execute(
            "DELETE FROM journey_executions WHERE thread_id = %s", (thread_id,)
        )
        await client.execute(
            "DELETE FROM journey_threads WHERE thread_id = %s", (thread_id,)
        )
    if journey_id:
        await client.execute(
            "DELETE FROM hold_requests WHERE journey_id = %s", (journey_id,)
        )
        await client.execute(
            "DELETE FROM journeys WHERE journey_id = %s", (journey_id,)
        )


async def test_the_function_refuses_to_oversell() -> None:
    """The capacity guard is in the function, below whatever the route checks.

    The order route pre-checks availability, so this exercises the guard the
    route cannot reach: a quantity above published capacity arriving at the
    function directly, as it would when inventory moved after the check.
    """
    client = get_rds_data_client()
    booking_id = f"HLD-ITEST{uuid.uuid4().hex[:6].upper()}"
    thread_id = f"itest-hold-{uuid.uuid4().hex[:8]}"
    journey_id = ""
    try:
        package_id, duration, price = await _a_package_with_room(client)
        capacity = await client.execute(
            "SELECT (availability ->> %s)::INTEGER AS n FROM trip_packages WHERE package_id = %s",
            (duration, package_id),
        )
        oversell = int(capacity[0]["n"]) + 1
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

        async with client.scoped_session(
            traveler_id=TRAVELER,
            agent_type="booking_agent",
            authorization=get_agentcore_identity().authorization_context(),
        ) as tx:
            journey_id = await ensure_journey(
                ScopedDb(client, tx), TRAVELER, thread_id, "AuroraDataApiSaver"
            )
            fingerprint = fingerprint_terms(
                normalize_hold_terms(package_id, duration, oversell, price)
            )
            with pytest.raises(Exception, match="insufficient_inventory"):
                await client.execute(
                    HOLD_SQL,
                    (
                        booking_id,
                        TRAVELER,
                        journey_id,
                        f"hrq_{booking_id}",
                        fingerprint,
                        package_id,
                        duration,
                        oversell,
                        price,
                        price * oversell,
                        expires,
                    ),
                    transaction_id=tx,
                )
    finally:
        await _cleanup(client, booking_id, thread_id, journey_id)


async def test_a_scope_that_does_not_match_the_session_is_refused() -> None:
    """RLS scope and the hold's traveler must agree, inside the function."""
    client = get_rds_data_client()
    booking_id = f"HLD-ITEST{uuid.uuid4().hex[:6].upper()}"
    try:
        package_id, duration, price = await _a_package_with_room(client)
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        async with client.scoped_session(
            traveler_id=TRAVELER,
            agent_type="booking_agent",
            authorization=get_agentcore_identity().authorization_context(),
        ) as tx:
            with pytest.raises(Exception, match="traveler_scope_mismatch"):
                await client.execute(
                    HOLD_SQL,
                    (
                        booking_id,
                        "trv_someone_else",
                        "jrn_whatever",
                        f"hrq_{booking_id}",
                        "fingerprint",
                        package_id,
                        duration,
                        1,
                        price,
                        price,
                        expires,
                    ),
                    transaction_id=tx,
                )
    finally:
        await _cleanup(client, booking_id, "", "")
