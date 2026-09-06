"""journey_store, against the live Aurora cluster.

These ran against a fake first, and the fake was worse than useless: it let a
failed statement be followed by a successful one, so it reported green on code
that raised `25P02 current transaction is aborted` the moment two workers
raced for real. Everything here talks to Aurora over the Data API.

Requires migration 007 and AWS credentials.
"""

from __future__ import annotations

import uuid
from typing import AsyncIterator

import pytest
import pytest_asyncio

from backend.agentcore.identity import get_agentcore_identity
from backend.db.journey_store import (
    ExecutionClaim,
    bind_thread,
    claim_execution,
    create_journey,
    ensure_journey,
    journey_for_thread,
    release_execution,
    renew_lease,
)
from backend.db.rds_data_client import get_rds_data_client

TRAVELER = "trv_meridian_demo"

# A lease that expired before the transaction started, which is what a worker
# that died mid-execution leaves behind. CURRENT_TIMESTAMP is frozen at
# transaction start, so zero would not be in the past.
ALREADY_EXPIRED = -1


async def _purge(client, thread_ids: list[str], journey_ids: list[str]) -> None:
    """Remove test rows as the admin role; the app role holds no DELETE."""
    for thread_id in thread_ids:
        await client.execute(
            "DELETE FROM journey_executions WHERE thread_id = %s", (thread_id,)
        )
        await client.execute(
            "UPDATE journeys SET active_thread_id = NULL WHERE active_thread_id = %s",
            (thread_id,),
        )
        await client.execute(
            "DELETE FROM journey_threads WHERE thread_id = %s", (thread_id,)
        )
    for journey_id in journey_ids:
        await client.execute(
            "DELETE FROM journeys WHERE journey_id = %s", (journey_id,)
        )


class _Session:
    """One scoped transaction plus the ids it created, for cleanup."""

    def __init__(self, client, transaction_id: str) -> None:
        self.client = client
        self.transaction_id = transaction_id
        self.threads: list[str] = []
        self.journeys: list[str] = []

    async def execute(self, sql: str, params: tuple = (), **kwargs):
        return await self.client.execute(
            sql, params, transaction_id=self.transaction_id
        )

    def new_thread(self) -> str:
        thread_id = f"itest-{uuid.uuid4().hex[:10]}"
        self.threads.append(thread_id)
        return thread_id

    async def journey(self) -> str:
        journey_id = await create_journey(self, TRAVELER, "AuroraDataApiSaver")
        self.journeys.append(journey_id)
        return journey_id

    async def bound_thread(self) -> tuple[str, str]:
        journey_id = await self.journey()
        thread_id = self.new_thread()
        await bind_thread(self, journey_id, thread_id)
        return journey_id, thread_id


@pytest_asyncio.fixture
async def session() -> AsyncIterator[_Session]:
    client = get_rds_data_client()
    created: _Session | None = None
    try:
        async with client.scoped_session(
            traveler_id=TRAVELER,
            agent_type="booking_agent",
            authorization=get_agentcore_identity().authorization_context(),
        ) as transaction_id:
            created = _Session(client, transaction_id)
            yield created
    finally:
        if created is not None:
            await _purge(client, created.threads, created.journeys)


# ---------------------------------------------------------------- identity


async def test_a_journey_records_its_owner_and_backend(session: _Session) -> None:
    journey_id = await session.journey()
    rows = await session.execute(
        "SELECT traveler_id, checkpoint_backend, status FROM journeys WHERE journey_id = %s",
        (journey_id,),
    )
    assert rows[0]["traveler_id"] == TRAVELER
    assert rows[0]["checkpoint_backend"] == "AuroraDataApiSaver"
    assert rows[0]["status"] == "active"


async def test_binding_a_thread_points_the_journey_at_it(session: _Session) -> None:
    journey_id, thread_id = await session.bound_thread()
    assert await journey_for_thread(session, thread_id) == journey_id
    rows = await session.execute(
        "SELECT active_thread_id FROM journeys WHERE journey_id = %s", (journey_id,)
    )
    assert rows[0]["active_thread_id"] == thread_id


async def test_rebinding_the_same_thread_is_idempotent(session: _Session) -> None:
    """Resume re-binds the thread the journey already owns."""
    journey_id, thread_id = await session.bound_thread()
    await bind_thread(session, journey_id, thread_id)
    rows = await session.execute(
        "SELECT count(*) AS n FROM journey_threads WHERE thread_id = %s", (thread_id,)
    )
    assert int(rows[0]["n"]) == 1


async def test_an_unbound_thread_has_no_journey(session: _Session) -> None:
    assert await journey_for_thread(session, "never-bound-anywhere") is None


async def test_ensure_journey_reuses_the_threads_existing_journey(
    session: _Session,
) -> None:
    journey_id, thread_id = await session.bound_thread()
    again = await ensure_journey(session, TRAVELER, thread_id, "AuroraDataApiSaver")
    assert again == journey_id


async def test_ensure_journey_creates_and_binds_a_new_thread(
    session: _Session,
) -> None:
    thread_id = session.new_thread()
    journey_id = await ensure_journey(
        session, TRAVELER, thread_id, "AuroraDataApiSaver"
    )
    session.journeys.append(journey_id)
    assert journey_id.startswith("jrn_")
    assert await journey_for_thread(session, thread_id) == journey_id


# ---------------------------------------------------------------- claiming


async def test_the_first_attempt_on_a_fresh_thread_is_one(session: _Session) -> None:
    journey_id, thread_id = await session.bound_thread()
    claim = await claim_execution(session, journey_id, thread_id, "worker_a")
    assert claim.claimed is True
    assert claim.attempt == 1
    assert isinstance(claim, ExecutionClaim)


async def test_a_thread_admits_one_running_execution(session: _Session) -> None:
    """The decisive one: losing must be a conflict, on a usable transaction.

    Letting the partial unique index raise instead leaves the transaction
    aborted, so the query naming the live owner comes back as 25P02.
    """
    journey_id, thread_id = await session.bound_thread()
    first = await claim_execution(session, journey_id, thread_id, "worker_a")

    second = await claim_execution(session, journey_id, thread_id, "worker_b")
    assert second.claimed is False
    assert second.conflict["worker_id"] == "worker_a"
    assert second.conflict["execution_id"] == first.execution_id

    # The transaction is still usable, which is the whole point.
    rows = await session.execute(
        "SELECT count(*) AS n FROM journey_executions WHERE thread_id = %s",
        (thread_id,),
    )
    assert int(rows[0]["n"]) == 1, "the losing claim must not have inserted a row"


async def test_an_expired_lease_is_taken_over(session: _Session) -> None:
    """A worker that died leaves an expired lease; the next one takes the slot."""
    journey_id, thread_id = await session.bound_thread()
    dead = await claim_execution(
        session, journey_id, thread_id, "worker_dead", lease_seconds=ALREADY_EXPIRED
    )
    assert dead.claimed is True

    taken = await claim_execution(session, journey_id, thread_id, "worker_live")
    assert taken.claimed is True, "an expired lease must not block a takeover"
    assert taken.attempt == 2

    rows = await session.execute(
        "SELECT status FROM journey_executions WHERE execution_id = %s",
        (dead.execution_id,),
    )
    assert rows[0]["status"] == "abandoned"


async def test_a_live_lease_is_not_taken_over(session: _Session) -> None:
    journey_id, thread_id = await session.bound_thread()
    await claim_execution(
        session, journey_id, thread_id, "worker_live", lease_seconds=300
    )
    stolen = await claim_execution(session, journey_id, thread_id, "worker_thief")
    assert stolen.claimed is False


async def test_the_claim_records_the_claiming_worker(session: _Session) -> None:
    journey_id, thread_id = await session.bound_thread()
    claim = await claim_execution(session, journey_id, thread_id, "worker_named")
    rows = await session.execute(
        "SELECT worker_id, status FROM journey_executions WHERE execution_id = %s",
        (claim.execution_id,),
    )
    assert rows[0]["worker_id"] == "worker_named"
    assert rows[0]["status"] == "running"


async def test_claiming_never_creates_a_thread(session: _Session) -> None:
    """An unbound thread cannot be claimed into existence: the FK refuses."""
    journey_id = await session.journey()
    with pytest.raises(Exception, match="journey_executions_thread_id_fkey"):
        await claim_execution(session, journey_id, "not-bound-to-anything", "worker_a")


# ------------------------------------------------------------------- lease


async def test_a_live_lease_renews(session: _Session) -> None:
    journey_id, thread_id = await session.bound_thread()
    claim = await claim_execution(session, journey_id, thread_id, "worker_a")
    assert await renew_lease(session, claim.execution_id) is True


async def test_a_released_execution_cannot_renew(session: _Session) -> None:
    """A worker whose slot ended must learn it, not keep heartbeating."""
    journey_id, thread_id = await session.bound_thread()
    claim = await claim_execution(session, journey_id, thread_id, "worker_a")
    await release_execution(session, claim.execution_id, "succeeded")
    assert await renew_lease(session, claim.execution_id) is False


async def test_release_records_the_terminal_status(session: _Session) -> None:
    journey_id, thread_id = await session.bound_thread()
    claim = await claim_execution(session, journey_id, thread_id, "worker_a")
    await release_execution(session, claim.execution_id, "failed")
    rows = await session.execute(
        """
        SELECT status, lease_expires_at, ended_at
          FROM journey_executions WHERE execution_id = %s
        """,
        (claim.execution_id,),
    )
    assert rows[0]["status"] == "failed"
    assert rows[0]["lease_expires_at"] is None
    assert rows[0]["ended_at"] is not None


async def test_a_released_slot_can_be_claimed_again(session: _Session) -> None:
    journey_id, thread_id = await session.bound_thread()
    first = await claim_execution(session, journey_id, thread_id, "worker_a")
    await release_execution(session, first.execution_id, "succeeded")
    second = await claim_execution(session, journey_id, thread_id, "worker_b")
    assert second.claimed is True
    assert second.attempt == 2
