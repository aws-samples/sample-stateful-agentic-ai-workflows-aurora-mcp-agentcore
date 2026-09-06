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
from typing import AsyncIterator

import pytest
import pytest_asyncio
from fastapi import HTTPException

from backend.db.rds_data_client import get_rds_data_client
from backend.http_auth import HttpPrincipal
from backend.routers.chat import OrderRequest, process_order

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

    def track(self, booking_id: str) -> str:
        self.booking_ids.append(booking_id)
        return booking_id

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

    async def purge(self) -> None:
        for booking_id in self.booking_ids:
            journeys = await self.client.execute(
                "SELECT journey_id FROM hold_requests WHERE booking_id = %s",
                (booking_id,),
            )
            await self.client.execute(
                "DELETE FROM hold_requests WHERE booking_id = %s", (booking_id,)
            )
            await self.client.execute(
                "DELETE FROM booking_lines WHERE booking_id = %s", (booking_id,)
            )
            await self.client.execute(
                "DELETE FROM bookings WHERE booking_id = %s", (booking_id,)
            )
            for row in journeys:
                await self.client.execute(
                    "DELETE FROM journey_threads WHERE journey_id = %s",
                    (row["journey_id"],),
                )
                await self.client.execute(
                    "DELETE FROM journeys WHERE journey_id = %s", (row["journey_id"],)
                )


@pytest_asyncio.fixture
async def holds() -> AsyncIterator[_Holds]:
    tracker = _Holds(get_rds_data_client())
    try:
        yield tracker
    finally:
        await tracker.purge()


async def _a_package_with_room(client, needed: int) -> tuple[str, str, int]:
    """A package and duration in the live catalog with room for `needed` seats."""
    rows = await client.execute(
        """
        SELECT package_id, availability FROM trip_packages
         WHERE availability IS NOT NULL ORDER BY package_id
        """,
        (),
    )
    for row in rows:
        availability = row["availability"]
        if isinstance(availability, str):
            availability = json.loads(availability)
        for duration, seats in (availability or {}).items():
            if int(seats) >= needed:
                return row["package_id"], duration, int(seats)
    pytest.skip(f"no catalog package publishes {needed} places on one duration")


async def test_a_hold_writes_a_booking_line_for_the_published_duration(
    holds: _Holds,
) -> None:
    package_id, duration, _seats = await _a_package_with_room(holds.client, 2)

    response = await process_order(
        OrderRequest(product_id=package_id, size=duration, quantity=2, phase=4),
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

    response = await process_order(
        OrderRequest(product_id=package_id, size=duration, quantity=1, phase=4),
        PRINCIPAL,
    )
    assert response.order is not None
    order_id = holds.track(response.order.order_id)

    identity = await holds.hold_request(order_id)
    assert identity["journey_id"].startswith("jrn_")
    assert identity["hold_request_id"] == f"hrq_{order_id}"
    assert identity["fingerprint"], "the hold must record a fingerprint of its terms"
    assert identity["traveler_id"] == TRAVELER, "the journey must belong to the caller"


async def test_the_response_reports_the_seats_the_database_counted(
    holds: _Holds,
) -> None:
    package_id, duration, seats = await _a_package_with_room(holds.client, 2)

    response = await process_order(
        OrderRequest(product_id=package_id, size=duration, quantity=2, phase=4),
        PRINCIPAL,
    )
    assert response.order is not None
    holds.track(response.order.order_id)
    assert f"Remaining package places: {seats - 2}" in response.message


async def test_asking_for_more_places_than_exist_reserves_nothing(
    holds: _Holds,
) -> None:
    """The route declines before the hold, and writes no booking either way."""
    package_id, duration, seats = await _a_package_with_room(holds.client, 1)
    if seats >= 12:
        pytest.skip("cannot request more than 12 travelers, so cannot oversell this one")

    before = await holds.client.execute(
        "SELECT count(*) AS n FROM booking_lines WHERE package_id = %s AND duration = %s",
        (package_id, duration),
    )

    response = await process_order(
        OrderRequest(
            product_id=package_id, size=duration, quantity=seats + 1, phase=4
        ),
        PRINCIPAL,
    )
    assert response.order is None
    assert "does not have enough places" in response.message

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
                product_id=package_id, size="900 nights", quantity=1, phase=4
            ),
            PRINCIPAL,
        )
    assert exc.value.status_code == 422

    left = await holds.client.execute(
        "SELECT count(*) AS n FROM booking_lines WHERE duration = %s", ("900 nights",)
    )
    assert int(left[0]["n"]) == 0
