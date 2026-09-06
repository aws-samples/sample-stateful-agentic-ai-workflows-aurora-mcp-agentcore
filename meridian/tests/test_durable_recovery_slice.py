"""The claim the chalk talk makes, tested end to end against real Aurora.

Each test maps to one step of the demonstrated sequence: a checkpoint is
committed where the audience can see it, a fresh worker picks the thread up,
two workers cannot both pick it up, and a hold survives a kill without being
placed twice.

Marked `database`, so `-m "not database"` still gives a fast offline pass.
Requires migrations 007 and 008 and AWS credentials.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import AsyncIterator

import pytest
import pytest_asyncio

from backend.agentcore.identity import get_agentcore_identity
from backend.agents.orchestration_05.hold_intent import prepare_hold_node
from backend.agents.orchestration_05.workflow import initialize_checkpoint_backend
from backend.db.journey_store import (
    ScopedDb,
    bind_thread,
    claim_execution,
    create_journey,
    release_execution,
)
from backend.db.rds_data_client import get_rds_data_client

pytestmark = pytest.mark.database

TRAVELER = "trv_meridian_demo"
PACKAGE = "TKY-003"
DURATION = "3 nights"
UNIT_PRICE = "1949.00"

HOLD_SQL = """
SELECT booking_id, status, replayed, seats_remaining
FROM create_courtesy_hold(
    %s::TEXT, %s::TEXT, %s::TEXT, %s::TEXT, %s::TEXT,
    %s::TEXT, %s::TEXT,
    %s::INTEGER, %s::NUMERIC, %s::NUMERIC,
    CURRENT_TIMESTAMP + interval '12 hours'
)
"""


class Slice:
    """One run of the slice, tracking everything it wrote."""

    def __init__(self, client) -> None:
        self.client = client
        self.journey_ids: list[str] = []
        self.thread_ids: list[str] = []
        self.booking_ids: list[str] = []

    def thread(self) -> str:
        thread_id = f"slice-{uuid.uuid4().hex[:10]}"
        self.thread_ids.append(thread_id)
        return thread_id

    def booking(self) -> str:
        booking_id = f"BKG-SLICE{uuid.uuid4().hex[:8].upper()}"
        self.booking_ids.append(booking_id)
        return booking_id

    def scoped(self):
        return self.client.scoped_session(
            traveler_id=TRAVELER,
            agent_type="booking_agent",
            authorization=get_agentcore_identity().authorization_context(),
        )

    async def journey_with_thread(self) -> tuple[str, str]:
        """A journey bound to a fresh thread, as the workflow would create it."""
        async with self.scoped() as tx:
            db = ScopedDb(self.client, tx)
            journey_id = await create_journey(db, TRAVELER, "AuroraDataApiSaver")
            self.journey_ids.append(journey_id)
            thread_id = self.thread()
            await bind_thread(db, journey_id, thread_id)
        return journey_id, thread_id

    async def purge(self) -> None:
        """Admin-role cleanup; the app role holds no DELETE on these tables."""
        for booking_id in self.booking_ids:
            await self.client.execute(
                "DELETE FROM hold_requests WHERE booking_id = %s", (booking_id,)
            )
            await self.client.execute(
                "DELETE FROM booking_lines WHERE booking_id = %s", (booking_id,)
            )
            await self.client.execute(
                "DELETE FROM bookings WHERE booking_id = %s", (booking_id,)
            )
        for thread_id in self.thread_ids:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                await self.client.execute(
                    f"DELETE FROM {table} WHERE thread_id = %s", (thread_id,)
                )
            await self.client.execute(
                "DELETE FROM journey_executions WHERE thread_id = %s", (thread_id,)
            )
            await self.client.execute(
                "UPDATE journeys SET active_thread_id = NULL WHERE active_thread_id = %s",
                (thread_id,),
            )
            await self.client.execute(
                "DELETE FROM journey_threads WHERE thread_id = %s", (thread_id,)
            )
        for journey_id in self.journey_ids:
            await self.client.execute(
                "DELETE FROM hold_requests WHERE journey_id = %s", (journey_id,)
            )
            await self.client.execute(
                "DELETE FROM journeys WHERE journey_id = %s", (journey_id,)
            )


@pytest_asyncio.fixture
async def slice_run() -> AsyncIterator[Slice]:
    run = Slice(get_rds_data_client())
    try:
        yield run
    finally:
        await run.purge()


# ------------------------------------------------- step 1: durable by config


async def test_the_demo_configuration_selects_a_durable_backend() -> None:
    """If this reports MemorySaver, every later claim in the talk is false."""
    backend = await initialize_checkpoint_backend()
    assert backend.durable is True
    assert backend.kind == "AuroraDataApiSaver"


# ---------------------------------------- step 2: the checkpoint is in Aurora


async def test_a_checkpoint_is_committed_where_the_room_can_see_it(
    slice_run: Slice,
) -> None:
    backend = await initialize_checkpoint_backend()
    thread_id = slice_run.thread()
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    checkpoint = {
        "v": 1,
        "id": "cp_slice_1",
        "ts": "2026-09-06T00:00:00+00:00",
        "channel_values": {"messages": ["kept"]},
        "channel_versions": {"messages": "1"},
        "versions_seen": {},
    }
    await backend.saver.aput(config, checkpoint, {"step": 1}, {"messages": "1"})

    rows = await slice_run.client.execute(
        "SELECT checkpoint_id FROM checkpoints WHERE thread_id = %s", (thread_id,)
    )
    assert rows and rows[0]["checkpoint_id"] == "cp_slice_1"

    loaded = await backend.saver.aget_tuple(config)
    assert loaded.checkpoint["channel_values"]["messages"] == ["kept"]


async def test_a_value_larger_than_one_row_survives(slice_run: Slice) -> None:
    """Workflow state is not small; the 64 KB row limit must not truncate it."""
    backend = await initialize_checkpoint_backend()
    thread_id = slice_run.thread()
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    big = "x" * (64 * 1024 + 17)
    await backend.saver.aput(
        config,
        {
            "v": 1,
            "id": "cp_big",
            "ts": "2026-09-06T00:00:00+00:00",
            "channel_values": {"messages": [big]},
            "channel_versions": {"messages": "1"},
            "versions_seen": {},
        },
        {"step": 1},
        {"messages": "1"},
    )
    loaded = await backend.saver.aget_tuple(config)
    assert loaded.checkpoint["channel_values"]["messages"][0] == big


# --------------------------------------------- step 3: a fresh worker resumes


async def test_a_fresh_worker_resumes_the_same_thread(slice_run: Slice) -> None:
    journey_id, thread_id = await slice_run.journey_with_thread()
    db = slice_run.client

    first = await claim_execution(db, journey_id, thread_id, "worker_01")
    assert first.claimed

    await release_execution(db, first.execution_id, "abandoned")

    second = await claim_execution(db, journey_id, thread_id, "worker_02")
    assert second.claimed
    assert second.attempt == first.attempt + 1
    assert second.worker_id != first.worker_id


async def test_simultaneous_resume_yields_exactly_one_execution(
    slice_run: Slice,
) -> None:
    """Two workers racing to resume is the failure the demo creates on purpose."""
    journey_id, thread_id = await slice_run.journey_with_thread()
    db = slice_run.client

    claims = await asyncio.gather(
        claim_execution(db, journey_id, thread_id, "worker_a"),
        claim_execution(db, journey_id, thread_id, "worker_b"),
        return_exceptions=True,
    )
    for claim in claims:
        assert not isinstance(claim, BaseException), f"claiming raised: {claim!r}"

    granted = [c for c in claims if c.claimed]
    refused = [c for c in claims if not c.claimed]
    assert len(granted) == 1, "the index must admit exactly one"
    assert len(refused) == 1
    assert refused[0].conflict["execution_id"] == granted[0].execution_id

    rows = await db.execute(
        "SELECT count(*) AS n FROM journey_executions "
        "WHERE thread_id = %s AND status = 'running'",
        (thread_id,),
    )
    assert int(rows[0]["n"]) == 1


# ------------------------------------------- step 4: one hold, however often


async def _place(slice_run: Slice, tx, journey_id, request_id, fingerprint, qty=1):
    price = float(UNIT_PRICE)
    return await slice_run.client.execute(
        HOLD_SQL,
        (
            slice_run.booking(),
            TRAVELER,
            journey_id,
            request_id,
            fingerprint,
            PACKAGE,
            DURATION,
            qty,
            price,
            price * qty,
        ),
        transaction_id=tx,
    )


async def test_replaying_a_request_yields_one_hold(slice_run: Slice) -> None:
    """A retry after the hold commits must not reserve a second time."""
    journey_id, _thread_id = await slice_run.journey_with_thread()
    request_id = f"hrq_{uuid.uuid4().hex[:12]}"

    async with slice_run.scoped() as tx:
        first = await _place(slice_run, tx, journey_id, request_id, "fp_same")
        second = await _place(slice_run, tx, journey_id, request_id, "fp_same")

        assert first[0]["replayed"] is False
        assert second[0]["replayed"] is True
        assert second[0]["booking_id"] == first[0]["booking_id"]

        rows = await slice_run.client.execute(
            "SELECT count(*) AS n FROM hold_requests "
            "WHERE journey_id = %s AND hold_request_id = %s",
            (journey_id, request_id),
            transaction_id=tx,
        )
        assert int(rows[0]["n"]) == 1


async def test_the_same_request_with_changed_terms_is_a_conflict(
    slice_run: Slice,
) -> None:
    journey_id, _thread_id = await slice_run.journey_with_thread()
    request_id = f"hrq_{uuid.uuid4().hex[:12]}"

    async with slice_run.scoped() as tx:
        await _place(slice_run, tx, journey_id, request_id, "fp_one", qty=1)
        with pytest.raises(Exception, match="hold_request_parameter_mismatch"):
            await _place(slice_run, tx, journey_id, request_id, "fp_two", qty=2)


# ----------------------- step 5: the kill the constraint alone cannot survive


async def test_an_interrupt_between_intent_and_hold_yields_one_hold(
    slice_run: Slice,
) -> None:
    """The worker dies after the intent is checkpointed, before the hold runs.

    This is the case a database constraint cannot cover on its own. If the
    resumed graph allocated a fresh hold_request_id, the two attempts would be
    two distinct requests and both would reserve inventory.
    """
    journey_id, thread_id = await slice_run.journey_with_thread()
    backend = await initialize_checkpoint_backend()
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    state = {
        "selected_package": PACKAGE,
        "duration": DURATION,
        "travelers_count": 1,
        "unit_price": UNIT_PRICE,
    }

    # First worker: establish the intent, commit it, then die.
    intent = prepare_hold_node(state)["hold_intent"]
    await backend.saver.aput(
        config,
        {
            "v": 1,
            "id": "cp_intent",
            "ts": "2026-09-06T00:00:00+00:00",
            "channel_values": {"hold_intent": intent},
            "channel_versions": {"hold_intent": "1"},
            "versions_seen": {},
        },
        {"step": 1},
        {"hold_intent": "1"},
    )

    # Second worker: a saver built fresh, reading only what Aurora holds.
    from backend.db.aurora_dataapi_saver import AuroraDataApiSaver

    restored = await AuroraDataApiSaver(get_rds_data_client()).aget_tuple(config)
    resumed = prepare_hold_node(
        dict(state, hold_intent=restored.checkpoint["channel_values"]["hold_intent"])
    )["hold_intent"]
    assert resumed["hold_request_id"] == intent["hold_request_id"], (
        "a resumed graph must reuse the checkpointed identity"
    )

    async with slice_run.scoped() as tx:
        first = await _place(
            slice_run, tx, journey_id, resumed["hold_request_id"], resumed["fingerprint"]
        )
        second = await _place(
            slice_run, tx, journey_id, resumed["hold_request_id"], resumed["fingerprint"]
        )
        assert second[0]["booking_id"] == first[0]["booking_id"]

        rows = await slice_run.client.execute(
            "SELECT count(*) AS n FROM bookings b "
            "JOIN hold_requests hr ON hr.booking_id = b.booking_id "
            "WHERE hr.journey_id = %s",
            (journey_id,),
            transaction_id=tx,
        )
        assert int(rows[0]["n"]) == 1, "one kill must not become two reservations"


# ------------------------------------------------------ step 6: authorization


async def test_another_traveler_cannot_use_the_journey(slice_run: Slice) -> None:
    journey_id, _thread_id = await slice_run.journey_with_thread()
    async with slice_run.scoped() as tx:
        with pytest.raises(
            Exception, match="traveler_scope_mismatch|journey_not_owned"
        ):
            await slice_run.client.execute(
                HOLD_SQL,
                (
                    slice_run.booking(),
                    "trv_demo_decoy",
                    journey_id,
                    f"hrq_{uuid.uuid4().hex[:12]}",
                    "fp",
                    PACKAGE,
                    DURATION,
                    1,
                    float(UNIT_PRICE),
                    float(UNIT_PRICE),
                ),
                transaction_id=tx,
            )
