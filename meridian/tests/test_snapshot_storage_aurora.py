"""AuroraSnapshotStorage against the live cluster: append-only rows, newest wins, RLS on read."""

import json
import uuid

import pytest
import pytest_asyncio

from strands.types.exceptions import StorageError

from backend.agentcore.identity import get_agentcore_identity
from backend.agents.phase_05_workflow.snapshot_storage import AuroraSnapshotStorage
from backend.db.journey_store import (
    ExecutionLeaseLostError,
    ScopedDb,
    bind_thread,
    claim_execution,
    create_journey,
)
from backend.db.rds_data_client import get_rds_data_client

pytestmark = pytest.mark.database

TRAVELER = "trv_meridian_demo"
DECOY = "trv_demo_decoy"


def key(session_id: str) -> str:
    return f"session/{session_id}/scopes/multiAgent/phase5/snapshots/snapshot_latest.json"


@pytest_asyncio.fixture
async def threads():
    client = get_rds_data_client()
    made: list[tuple[str, str | None]] = []
    yield client, made
    for thread_id, journey_id in made:
        await client.execute("DELETE FROM workflow_snapshots WHERE session_id = %s", (thread_id,))
        await client.execute("DELETE FROM journey_executions WHERE thread_id = %s", (thread_id,))
        await client.execute(
            "UPDATE journeys SET active_thread_id = NULL WHERE active_thread_id = %s", (thread_id,)
        )
        await client.execute("DELETE FROM journey_threads WHERE thread_id = %s", (thread_id,))
        if journey_id:
            await client.execute("DELETE FROM journeys WHERE journey_id = %s", (journey_id,))


async def bound_thread(client, made, traveler_id: str, thread_id: str | None = None) -> str:
    """A thread bound to a journey of ``traveler_id``, written as the master role."""
    thread_id = thread_id or f"snap-{uuid.uuid4().hex[:10]}"
    journey_id = await create_journey(client, traveler_id, "Aurora workflow_snapshots")
    await bind_thread(client, journey_id, thread_id)
    made.append((thread_id, journey_id))
    return thread_id


async def writer(client, made, traveler_id: str = TRAVELER, **kwargs):
    """A bound thread with a running execution, and the storage that execution writes through."""
    thread_id = await bound_thread(client, made, traveler_id, kwargs.pop("thread_id", None))
    claim = await claim_execution(client, made[-1][1], thread_id, "worker-test")
    assert claim.claimed
    storage = AuroraSnapshotStorage(
        client, session_id=thread_id, traveler_id=traveler_id,
        execution_id=claim.execution_id, worker_id="worker-test", **kwargs,
    )
    return thread_id, claim.execution_id, storage


EXECUTING = b'{"data": {"state": {"status": "executing"}}}'


async def rows_for(client, session_id: str):
    return await client.execute(
        "SELECT storage_key, traveler_id, execution_id, worker_id FROM workflow_snapshots "
        "WHERE session_id = %s ORDER BY snapshot_seq", (session_id,),
    )


async def test_writes_append_and_the_newest_row_wins(threads):
    client, made = threads
    timings: list[int] = []
    thread_id, execution_id, storage = await writer(client, made, on_write=timings.append)
    first = json.dumps({"data": {"state": {"status": "executing"}}}).encode()
    second = json.dumps({"data": {"state": {"status": "interrupted"}}}).encode()
    await storage.write(key(thread_id), first)
    await storage.write(key(thread_id), second)

    newest = json.loads(await storage.read(key(thread_id)))
    assert newest["data"]["state"]["status"] == "interrupted"
    rows = await client.execute(
        "SELECT status, traveler_id, execution_id, worker_id FROM workflow_snapshots "
        "WHERE session_id = %s ORDER BY snapshot_seq", (thread_id,),
    )
    assert [r["status"] for r in rows] == ["executing", "interrupted"]
    assert {(r["traveler_id"], r["execution_id"], r["worker_id"]) for r in rows} == {
        (TRAVELER, execution_id, "worker-test")
    }
    assert len(timings) == 2 and all(ms >= 0 for ms in timings)


async def test_a_row_carries_the_bound_identity_not_the_one_the_snapshot_names(threads):
    client, made = threads
    thread_id, execution_id, storage = await writer(client, made)
    forged = json.dumps({
        "traveler_id": DECOY, "execution_id": "exe_forged", "worker_id": "worker-forged",
        "data": {"state": {"status": "executing", "traveler_id": DECOY,
                           "execution_id": "exe_forged", "worker_id": "worker-forged"}},
    }).encode()
    await storage.write(key(thread_id), forged)

    rows = await rows_for(client, thread_id)
    assert [(r["traveler_id"], r["execution_id"], r["worker_id"]) for r in rows] == [
        (TRAVELER, execution_id, "worker-test")
    ]
    assert "worker-forged" in (await storage.read(key(thread_id))).decode()


async def test_missing_key_reads_none_and_listing_stays_in_the_session(threads):
    client, made = threads
    thread_id, _execution_id, storage = await writer(client, made)
    assert await storage.read(key(thread_id)) is None
    await storage.write(key(thread_id), EXECUTING)
    assert await storage.list(f"session/{thread_id}/") == [key(thread_id)]
    assert await storage.list("") == [key(thread_id)]


async def test_listing_treats_percent_and_underscore_literally_and_stays_in_its_session(threads):
    client, made = threads
    stem = uuid.uuid4().hex[:8]
    mine, _e1, mine_storage = await writer(client, made, thread_id=f"snap-{stem}%x_y")
    near, _e2, near_storage = await writer(client, made, thread_id=f"snap-{stem}ZZxAy")
    await mine_storage.write(f"session/{mine}/scopes/a_b/1", EXECUTING)
    await mine_storage.write(f"session/{mine}/scopes/axb/1", EXECUTING)
    await mine_storage.write(f"session/{mine}/scopes/p%q/1", EXECUTING)
    await mine_storage.write(f"session/{mine}/scopes/pzzq/1", EXECUTING)
    await near_storage.write(f"session/{near}/scopes/a_b/1", EXECUTING)

    assert await mine_storage.list(f"session/{mine}/scopes/a_b/") == [
        f"session/{mine}/scopes/a_b/1"
    ]
    assert await mine_storage.list(f"session/{mine}/scopes/p%q/") == [
        f"session/{mine}/scopes/p%q/1"
    ]
    assert len(await mine_storage.list("")) == 4
    assert await near_storage.list("") == [f"session/{near}/scopes/a_b/1"]
    assert await mine_storage.list(f"session/{near}/") == []
    assert [r["storage_key"] for r in await rows_for(client, near)] == [
        f"session/{near}/scopes/a_b/1"
    ]


async def test_keys_outside_the_session_are_refused(threads):
    client, made = threads
    thread_id, _execution_id, storage = await writer(client, made)
    other = f"snap-other-{uuid.uuid4().hex[:8]}"
    with pytest.raises(StorageError, match="outside workflow session"):
        await storage.write(key(other), b"{}")
    with pytest.raises(StorageError, match="outside workflow session"):
        await storage.read(key(other))
    for session_id in (thread_id, other):
        assert await rows_for(client, session_id) == []


async def test_a_storage_without_an_execution_is_read_only(threads):
    client, made = threads
    thread_id = await bound_thread(client, made, TRAVELER)
    storage = AuroraSnapshotStorage(
        client, session_id=thread_id, traveler_id=TRAVELER, execution_id=None, worker_id="w"
    )
    with pytest.raises(StorageError, match="read-only"):
        await storage.write(key(thread_id), EXECUTING)
    assert await rows_for(client, thread_id) == []


async def test_snapshots_cannot_be_deleted_through_the_storage(threads):
    client, made = threads
    thread_id, _execution_id, storage = await writer(client, made)
    await storage.write(key(thread_id), EXECUTING)
    before = await rows_for(client, thread_id)
    assert len(before) == 1
    with pytest.raises(StorageError, match="append-only"):
        await storage.delete(key(thread_id))
    assert await rows_for(client, thread_id) == before
    assert await storage.read(key(thread_id)) == EXECUTING


async def test_a_scoped_reader_sees_only_its_own_bound_snapshots(threads):
    """Negative controls have rows: a decoy's bound thread, and an unbound thread of ours."""
    client, made = threads
    mine = await bound_thread(client, made, TRAVELER)
    decoys = await bound_thread(client, made, DECOY)
    unbound = f"snap-unbound-{uuid.uuid4().hex[:8]}"
    made.append((unbound, None))
    for session_id, traveler_id in ((mine, TRAVELER), (decoys, DECOY), (unbound, TRAVELER)):
        await client.execute(
            "INSERT INTO workflow_snapshots (storage_key, session_id, traveler_id, snapshot) "
            "VALUES (%s, %s, %s, %s::jsonb)",
            (key(session_id), session_id, traveler_id, EXECUTING.decode()),
        )

    master = await client.execute(
        "SELECT session_id FROM workflow_snapshots WHERE session_id IN (%s, %s, %s)",
        (mine, decoys, unbound),
    )
    assert len(master) == 3, "the negative controls must exist for the scoped read to mean anything"
    async with client.scoped_session(
        traveler_id=TRAVELER, agent_type="booking_agent",
        authorization=get_agentcore_identity().authorization_context(),
    ) as tx:
        visible = await ScopedDb(client, tx).execute(
            "SELECT session_id FROM workflow_snapshots WHERE session_id IN (%s, %s, %s)",
            (mine, decoys, unbound),
        )
    assert [row["session_id"] for row in visible] == [mine]


ALREADY_EXPIRED = -1


async def test_a_stalled_worker_cannot_append_after_another_worker_took_the_thread(threads):
    client, made = threads
    thread_id = await bound_thread(client, made, TRAVELER)
    journey_id = made[-1][1]
    stalled = await claim_execution(
        client, journey_id, thread_id, "worker-stalled", lease_seconds=ALREADY_EXPIRED
    )
    taker = await claim_execution(client, journey_id, thread_id, "worker-taker")
    assert stalled.claimed and taker.claimed

    fenced = AuroraSnapshotStorage(
        client, session_id=thread_id, traveler_id=TRAVELER,
        execution_id=stalled.execution_id, worker_id="worker-stalled",
    )
    with pytest.raises(ExecutionLeaseLostError, match=stalled.execution_id) as lost:
        await fenced.write(key(thread_id), b'{"data": {"state": {"status": "executing"}}}')
    assert thread_id in str(lost.value)

    rows = await client.execute(
        "SELECT execution_id FROM workflow_snapshots WHERE session_id = %s", (thread_id,)
    )
    assert rows == [], "the stalled worker's snapshot must not exist"
    states = await client.execute(
        "SELECT execution_id, status FROM journey_executions WHERE thread_id = %s", (thread_id,)
    )
    assert {r["execution_id"]: r["status"] for r in states} == {
        stalled.execution_id: "abandoned", taker.execution_id: "running",
    }

    current = AuroraSnapshotStorage(
        client, session_id=thread_id, traveler_id=TRAVELER,
        execution_id=taker.execution_id, worker_id="worker-taker",
    )
    await current.write(key(thread_id), b'{"data": {"state": {"status": "executing"}}}')
    rows = await client.execute(
        "SELECT execution_id FROM workflow_snapshots WHERE session_id = %s", (thread_id,)
    )
    assert [r["execution_id"] for r in rows] == [taker.execution_id]


async def test_a_snapshot_past_the_data_api_row_limit_round_trips(threads):
    client, made = threads
    thread_id, _, storage = await writer(client, made)
    big = {"data": {"state": {"status": "executing", "padding": "é" * 120_000}}}
    await storage.write(key(thread_id), json.dumps(big).encode())

    assert json.loads(await storage.read(key(thread_id))) == big
    size = await client.execute(
        "SELECT octet_length(snapshot::TEXT) AS n FROM workflow_snapshots WHERE session_id = %s",
        (thread_id,),
    )
    assert size[0]["n"] > 64 * 1024


async def test_a_snapshot_over_the_read_ceiling_is_refused_and_not_written(threads):
    client, made = threads
    thread_id, _, storage = await writer(client, made)
    huge = json.dumps({"data": {"state": {"padding": "x" * 950_000}}}).encode()

    with pytest.raises(StorageError, match="900000"):
        await storage.write(key(thread_id), huge)
    assert await rows_for(client, thread_id) == []
