"""Against the live cluster: row-level security hides Jordan's rows from the decoy's scope."""

import asyncio
import uuid

import pytest

from backend.db.rds_data_client import get_rds_data_client
from scripts.identity_probes.effects import AuroraCleanup, AuroraPort
from scripts.identity_probes.probes import DECOY_TRAVELER, JORDAN_TRAVELER
from scripts.kill_and_resume_proof import pinned_to_traveler


@pytest.mark.database
def test_the_decoy_sees_none_of_jordans_rows_and_jordan_sees_all_of_them():
    port = AuroraPort(get_rds_data_client())

    baseline = port.baseline_count(JORDAN_TRAVELER)

    assert baseline > 0
    assert port.scoped_count(DECOY_TRAVELER, JORDAN_TRAVELER) == 0
    assert port.scoped_count(JORDAN_TRAVELER, JORDAN_TRAVELER) == baseline


INSERT_BOOKING = ("INSERT INTO bookings (booking_id, traveler_id, status, total_amount) "
                  "VALUES (%s, %s, 'held', 1)")


INSERT_JOURNEY = ("INSERT INTO journeys (journey_id, traveler_id, checkpoint_backend) "
                  "VALUES (%s, %s, 'aurora')")
INSERT_HOLD_REQUEST = ("INSERT INTO hold_requests (journey_id, hold_request_id, booking_id, "
                       "fingerprint, thread_id) VALUES (%s, %s, %s, 'idp-test', 'idp-test')")


async def insert_booking(client, booking: str, journey: str) -> None:
    """A booking with a hold request on it, so the release must delete the child row first."""
    async with pinned_to_traveler(client, JORDAN_TRAVELER) as transaction:
        await client.execute(INSERT_JOURNEY, (journey, JORDAN_TRAVELER), transaction_id=transaction)
        await client.execute(INSERT_BOOKING, (booking, JORDAN_TRAVELER), transaction_id=transaction)
        await client.execute(
            INSERT_HOLD_REQUEST, (journey, f"req-{booking}", booking), transaction_id=transaction)


async def drop_journey(client, journey: str) -> None:
    await client.execute("DELETE FROM journeys WHERE journey_id = %s", (journey,))


@pytest.mark.database
def test_the_unscoped_tables_the_proof_reads_show_their_rows_to_this_role():
    AuroraPort(get_rds_data_client()).preflight()


@pytest.mark.database
def test_a_booking_is_found_pinned_listed_as_new_released_and_gone():
    client = get_rds_data_client()
    port, cleanup = AuroraPort(client), AuroraCleanup(client)
    baseline = {t: frozenset(port.booking_ids(t)) for t in (JORDAN_TRAVELER, DECOY_TRAVELER)}
    booking = f"HLD-IDP{uuid.uuid4().hex[:8].upper()}"
    pair = (JORDAN_TRAVELER, booking)
    journey = f"idp-test-{booking}"
    prefix = "phase5-proof-idp-live-test"
    try:
        asyncio.run(insert_booking(client, booking, journey))
        assert booking in port.booking_ids(JORDAN_TRAVELER)
        assert booking not in port.booking_ids(DECOY_TRAVELER)
        assert cleanup.leftovers(prefix, baseline) == [f"booking {booking} of {JORDAN_TRAVELER}"]
    finally:
        if booking in port.booking_ids(JORDAN_TRAVELER):
            cleanup.release_bookings([pair])
        asyncio.run(drop_journey(client, journey))

    assert cleanup.leftovers(prefix, baseline) == []
