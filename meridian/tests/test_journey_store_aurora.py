"""journey_store against real Aurora.

A fake that lets a failed statement be followed by a successful one hides the
single most important property of this code: PostgreSQL aborts a transaction
when a statement raises, so recovering from a unique violation by running
another query in the same transaction fails with 25P02. That bug passed every
unit test and only appeared here.

Opt in with MERIDIAN_AURORA_TESTS=1. Requires migration 007 and AWS credentials.
"""

from __future__ import annotations

import os

import pytest

from backend.agentcore.identity import get_agentcore_identity
from backend.db.journey_store import (
    bind_thread,
    claim_execution,
    create_journey,
    release_execution,
    renew_lease,
)
from backend.db.rds_data_client import get_rds_data_client

pytestmark = pytest.mark.skipif(
    os.getenv("MERIDIAN_AURORA_TESTS") != "1",
    reason="set MERIDIAN_AURORA_TESTS=1 to run against the live cluster",
)

TRAVELER = "trv_meridian_demo"


class _Scoped:
    """Route journey_store's statements through one scoped transaction."""

    def __init__(self, client, transaction_id: str) -> None:
        self.client = client
        self.transaction_id = transaction_id

    async def execute(self, sql: str, params: tuple = (), **kwargs):
        return await self.client.execute(
            sql, params, transaction_id=self.transaction_id
        )


async def test_a_thread_admits_one_execution_at_a_time() -> None:
    client = get_rds_data_client()
    thread_id = ""
    journey_id = ""
    try:
        async with client.scoped_session(
            traveler_id=TRAVELER,
            agent_type="booking_agent",
            authorization=get_agentcore_identity().authorization_context(),
        ) as tx:
            db = _Scoped(client, tx)

            journey_id = await create_journey(db, TRAVELER, "AuroraDataApiSaver")
            thread_id = f"itest-{journey_id}"

            await bind_thread(db, journey_id, thread_id)
            await bind_thread(db, journey_id, thread_id)

            first = await claim_execution(db, journey_id, thread_id, "worker_a")
            assert first.claimed is True
            assert first.attempt == 1

            # The decisive assertion: losing the race returns a conflict on a
            # transaction that is still usable, rather than raising 25P02.
            second = await claim_execution(db, journey_id, thread_id, "worker_b")
            assert second.claimed is False
            assert second.conflict["worker_id"] == "worker_a"
            assert second.conflict["execution_id"] == first.execution_id

            assert await renew_lease(db, first.execution_id) is True
            await release_execution(db, first.execution_id, "succeeded")
            assert await renew_lease(db, first.execution_id) is False

            third = await claim_execution(db, journey_id, thread_id, "worker_c")
            assert third.claimed is True, "a released slot must be claimable"
            assert third.attempt == 2
    finally:
        # Outside the scoped session, so this runs as the admin role. The app
        # role deliberately holds no DELETE on the journey tables.
        if thread_id:
            await client.execute(
                "DELETE FROM journey_executions WHERE thread_id = %s", (thread_id,)
            )
            await client.execute(
                "UPDATE journeys SET active_thread_id = NULL WHERE journey_id = %s",
                (journey_id,),
            )
            await client.execute(
                "DELETE FROM journey_threads WHERE thread_id = %s", (thread_id,)
            )
            await client.execute(
                "DELETE FROM journeys WHERE journey_id = %s", (journey_id,)
            )
