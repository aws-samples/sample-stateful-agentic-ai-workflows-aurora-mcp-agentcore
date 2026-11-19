"""Courtesy holds, driven through the order route against live Aurora.

These used a fake database and asserted on the SQL it recorded. That proved
the route built a string, not that Aurora accepted it: the fake happily
returned rows for a function signature the cluster no longer had, and it
returned TEXT where the real columns are VARCHAR, which is a type error
PostgreSQL raises and a dict never will. The route now talks to the cluster.

Requires migrations 007 and 008 and AWS credentials.
"""

from __future__ import annotations

import json
import uuid
from typing import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import HTTPException

from backend.db.rds_data_client import get_rds_data_client
from backend.http_auth import HttpPrincipal
from backend.routers.chat import OrderRequest, process_order
from backend.routers.memory import get_memory_profile
from scripts.agentcore_caller import caller_scope
from tests.live_support import (
    cleanup_run,
    report_cleanup,
)

pytestmark = pytest.mark.database

TRAVELER = "trv_meridian_demo"

PRINCIPAL = HttpPrincipal(
    subject_id="test-client",
    traveler_id=TRAVELER,
    authentication="test",
)


class _Holds:
    """Tracks the holds a test places so they can be removed afterwards."""

    def __init__(self, client) -> None:
        self.client = client
        self.booking_ids: list[str] = []
        self.threads: list[str] = []

    def track(self, booking_id: str) -> str:
        self.booking_ids.append(booking_id)
        return booking_id

    def track_thread(self, thread_id: str) -> str:
        """Release every hold on this journey thread, even one whose reply never arrived."""
        self.threads.append(thread_id)
        return thread_id

    async def thread_bookings(self, thread_id: str) -> list[str]:
        """The bookings the journey on this thread holds, read from Aurora."""
        rows = await self.client.execute(
            "SELECT hr.booking_id FROM hold_requests hr JOIN journey_threads jt "
            "ON jt.journey_id = hr.journey_id WHERE jt.thread_id = %s",
            (thread_id,),
        )
        return [row["booking_id"] for row in rows]

    async def booking_line(self, booking_id: str) -> dict:
        rows = await self.client.execute(
            """
            SELECT package_id, duration, travelers_count, unit_price
              FROM booking_lines WHERE booking_id = %s
            """,
            (booking_id,),
        )
        assert rows, f"no booking line was written for {booking_id}"
        return rows[0]

    async def hold_request(self, booking_id: str) -> dict:
        rows = await self.client.execute(
            """
            SELECT hr.journey_id, hr.hold_request_id, hr.fingerprint, j.traveler_id
              FROM hold_requests hr
              JOIN journeys j ON j.journey_id = hr.journey_id
             WHERE hr.booking_id = %s
            """,
            (booking_id,),
        )
        assert rows, f"no hold request identity was written for {booking_id}"
        return rows[0]

    async def release(self) -> None:
        """Give back every hold on the tracked threads and bookings; delete nothing.

        Every hold is attempted before any problem is reported, and the cleanup never stops
        at the first failure.
        """
        report_cleanup(await cleanup_run(self.client, TRAVELER, test="stale_quote",
                                         threads=self.threads, bookings=self.booking_ids))


@pytest.fixture(autouse=True)
def signed_in_as_jordan():
    """In jwt mode the order route calls AgentCore with the signed-in traveler's token.

    ``caller_scope`` mints Jordan's Cognito access token and binds it for the test; in iam mode
    it binds nothing.
    """
    with caller_scope("jordan"):
        yield


@pytest_asyncio.fixture
async def holds() -> AsyncIterator[_Holds]:
    """Every hold a test tracks goes back through the governed release; every row stays.

    The audit export may already hold the action audit rows; deleting them would leave a
    lasting row-deleted finding in the shared database's verification.
    """
    tracker = _Holds(get_rds_data_client())
    try:
        yield tracker
    finally:
        await tracker.release()


async def _quote(client, package_id: str, duration: str, quantity: int) -> dict:
    """The terms the Hold action shows for this package, read from the live catalog."""
    row = await client.execute_one(
        "SELECT ROUND(price_per_person * 100)::BIGINT AS cents FROM trip_packages "
        "WHERE package_id = %s",
        (package_id,),
    )
    unit = int(row["cents"])
    return {"unit_price_cents": unit, "total_cents": unit * quantity, "currency": "USD",
            "size": duration, "quantity": quantity}


async def _a_package_with_room(client, needed: int, *, oversell=False) -> tuple[str, str, int]:
    """Select live inventory inside the saved budget and Cedar party limit.

    Budget and party denial are tested separately. Inventory tests must reach
    Aurora's capacity check, and must count active holds as well as bookings.
    """
    profile = await get_memory_profile(TRAVELER, PRINCIPAL)
    cap = profile.budget_ceiling_per_traveler_cents
    assert cap is not None, "The live hold test needs the seeded per-traveler budget"
    rows = await client.execute(
        """
        SELECT package_id, availability FROM trip_packages
         WHERE availability IS NOT NULL AND price_per_person * 100 <= %s
         ORDER BY package_id
        """,
        (cap,),
    )
    for row in rows:
        availability = row["availability"]
        if isinstance(availability, str):
            availability = json.loads(availability)
        for duration, seats in (availability or {}).items():
            reserved = await client.execute_one(
                "SELECT COALESCE(SUM(bl.travelers_count), 0) AS n "
                "FROM booking_lines bl JOIN bookings b USING (booking_id) "
                "WHERE bl.package_id = %s AND bl.duration = %s "
                "AND (b.status = 'confirmed' OR (b.status = 'held' "
                "AND b.hold_expires_at > CURRENT_TIMESTAMP))",
                (row["package_id"], duration),
            )
            remaining = int(seats) - int(reserved["n"])
            if remaining >= needed and (not oversell or remaining < 6):
                return row["package_id"], duration, remaining
    pytest.fail(f"No eligible live package with {needed} places; replenish demo inventory")


async def test_a_hold_writes_a_booking_line_for_the_published_duration(
    holds: _Holds,
) -> None:
    package_id, duration, _seats = await _a_package_with_room(holds.client, 2)

    response = await process_order(
        OrderRequest(product_id=package_id, size=duration, quantity=2, phase=4,
                     quote=await _quote(holds.client, package_id, duration, 2)),
        PRINCIPAL,
    )

    assert response.order is not None
    holds.track(response.order.order_id)
    assert response.order.items[0].size == duration

    line = await holds.booking_line(response.order.order_id)
    assert line["package_id"] == package_id
    assert line["duration"] == duration
    assert int(line["travelers_count"]) == 2


async def test_a_hold_is_recorded_under_a_journey_and_a_request_identity(
    holds: _Holds,
) -> None:
    """Without these the hold has no identity to be idempotent against."""
    package_id, duration, _seats = await _a_package_with_room(holds.client, 1)

    request = OrderRequest(product_id=package_id, size=duration, quantity=1, phase=4,
                           conversation_id=f"hold-test-{uuid.uuid4().hex[:12]}",
                           quote=await _quote(holds.client, package_id, duration, 1))
    response = await process_order(
        request,
        PRINCIPAL,
    )
    assert response.order is not None
    order_id = holds.track(response.order.order_id)

    identity = await holds.hold_request(order_id)
    assert identity["journey_id"].startswith("jrn_")
    assert identity["hold_request_id"].startswith("hrq_")
    replay = await process_order(request, PRINCIPAL)
    assert replay.order is not None and replay.order.order_id == order_id
    assert await holds.hold_request(order_id) == identity
    assert identity["fingerprint"], "the hold must record a fingerprint of its terms"
    assert identity["traveler_id"] == TRAVELER, "the journey must belong to the caller"


async def test_the_response_reports_the_seats_the_database_counted(
    holds: _Holds,
) -> None:
    package_id, duration, seats = await _a_package_with_room(holds.client, 2)

    response = await process_order(
        OrderRequest(product_id=package_id, size=duration, quantity=2, phase=4,
                     quote=await _quote(holds.client, package_id, duration, 2)),
        PRINCIPAL,
    )
    assert response.order is not None
    holds.track(response.order.order_id)
    assert response.order.seats_remaining == seats - 2


async def test_asking_for_more_places_than_exist_reserves_nothing(
    holds: _Holds,
) -> None:
    """The route declines before the hold, and writes no booking either way."""
    package_id, duration, seats = await _a_package_with_room(holds.client, 1, oversell=True)

    before = await holds.client.execute(
        "SELECT count(*) AS n FROM booking_lines WHERE package_id = %s AND duration = %s",
        (package_id, duration),
    )

    response = await process_order(
        OrderRequest(
            product_id=package_id, size=duration, quantity=seats + 1, phase=4,
            quote=await _quote(holds.client, package_id, duration, seats + 1),
        ),
        PRINCIPAL,
    )
    assert response.order is None
    assert any(activity.details == "insufficient_inventory" for activity in response.activities)

    after = await holds.client.execute(
        "SELECT count(*) AS n FROM booking_lines WHERE package_id = %s AND duration = %s",
        (package_id, duration),
    )
    assert int(after[0]["n"]) == int(before[0]["n"])


async def test_an_unpublished_duration_is_refused_before_any_hold(
    holds: _Holds,
) -> None:
    package_id, _duration, _seats = await _a_package_with_room(holds.client, 1)

    with pytest.raises(HTTPException) as exc:
        await process_order(
            OrderRequest(
                product_id=package_id, size="900 nights", quantity=1, phase=4,
                quote=await _quote(holds.client, package_id, "900 nights", 1),
            ),
            PRINCIPAL,
        )
    assert exc.value.status_code == 422

    left = await holds.client.execute(
        "SELECT count(*) AS n FROM booking_lines WHERE duration = %s", ("900 nights",)
    )
    assert int(left[0]["n"]) == 0


async def test_a_quote_the_catalog_no_longer_matches_reserves_nothing_until_it_is_renewed(
    holds: _Holds,
) -> None:
    """The traveler saw an older price: 409 with the current terms, then the new approval holds."""
    package_id, duration, _seats = await _a_package_with_room(holds.client, 1)
    current = await _quote(holds.client, package_id, duration, 1)
    stale = {**current, "unit_price_cents": current["unit_price_cents"] - 100,
             "total_cents": current["total_cents"] - 100}
    conversation = f"hold-test-{uuid.uuid4().hex[:12]}"
    # Phase 4 holds use this journey thread; tracking it first also cleans up a lost reply.
    thread = holds.track_thread(f"concierge:{conversation}")

    with pytest.raises(HTTPException) as refused:
        await process_order(OrderRequest(
            product_id=package_id, size=duration, quantity=1, phase=4,
            conversation_id=conversation, quote=stale), PRINCIPAL)

    assert refused.value.status_code == 409
    assert refused.value.detail["code"] == "price_changed"
    assert refused.value.detail["quote"] == current
    assert await holds.thread_bookings(thread) == [], "the refused quote reserved nothing"

    renewed = await process_order(OrderRequest(
        product_id=package_id, size=duration, quantity=1, phase=4,
        conversation_id=conversation, quote=current), PRINCIPAL)
    assert renewed.order is not None
    holds.track(renewed.order.order_id)
    assert await holds.thread_bookings(thread) == [renewed.order.order_id]
    line = await holds.booking_line(renewed.order.order_id)
    assert int(round(float(line["unit_price"]) * 100)) == current["unit_price_cents"]
    assert renewed.order.total == current["total_cents"] / 100
