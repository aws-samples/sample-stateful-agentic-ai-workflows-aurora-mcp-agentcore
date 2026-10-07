"""AuroraSnapshotStorage against the live cluster: append-only rows, newest wins, RLS on read."""

import json
import uuid

import pytest
import pytest_asyncio

from strands.types.exceptions import StorageError

from backend.agentcore.identity import get_agentcore_identity
from backend.agents.phase_05_workflow.snapshot_storage import AuroraSnapshotStorage
from backend.db.journey_store import ScopedDb, bind_thread, create_journey
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
        await client.execute("UPDATE journeys SET active_thread_id = NULL WHERE active_thread_id = %s", (thread_id,))
        await client.execute("DELETE FROM journey_threads WHERE thread_id = %s", (thread_id,))
        if journey_id:
            await client.execute("DELETE FROM journeys WHERE journey_id = %s", (journey_id,))


async def bound_thread(client, made, traveler_id: str) -> str:
    """A thread bound to a journey of ``traveler_id``, written as the master role."""
    thread_id = f"snap-{uuid.uuid4().hex[:10]}"
    journey_id = await create_journey(client, traveler_id, "Aurora workflow_snapshots")
    await bind_thread(client, journey_id, thread_id)
    made.append((thread_id, journey_id))
    return thread_id


async def test_writes_append_and_the_newest_row_wins(threads):
    client, made = threads
    thread_id = await bound_thread(client, made, TRAVELER)
    timings: list[int] = []
    storage = AuroraSnapshotStorage(
        client, session_id=thread_id, traveler_id=TRAVELER, execution_id="exe_test",
        worker_id="worker-test", on_write=timings.append,
    )
    first = json.dumps({"data": {"state": {"status": "executing"}}}).encode()
    second = json.dumps({"data": {"state": {"status": "interrupted"}}}).encode()
    await storage.write(key(thread_id), first)
    await storage.write(key(thread_id), second)

    assert json.loads(await storage.read(key(thread_id)))["data"]["state"]["status"] == "interrupted"
    rows = await client.execute(
        "SELECT status, traveler_id, execution_id, worker_id FROM workflow_snapshots "
        "WHERE session_id = %s ORDER BY snapshot_seq", (thread_id,),
    )
    assert [r["status"] for r in rows] == ["executing", "interrupted"]
    assert {(r["traveler_id"], r["execution_id"], r["worker_id"]) for r in rows} == {
        (TRAVELER, "exe_test", "worker-test")
    }
    assert len(timings) == 2 and all(ms >= 0 for ms in timings)


async def test_missing_key_reads_none_and_listing_stays_in_the_session(threads):
    client, made = threads
    thread_id = await bound_thread(client, made, TRAVELER)
    storage = AuroraSnapshotStorage(client, session_id=thread_id, traveler_id=TRAVELER)
    assert await storage.read(key(thread_id)) is None
    await storage.write(key(thread_id), b'{"data": {"state": {"status": "executing"}}}')
    assert await storage.list(f"session/{thread_id}/") == [key(thread_id)]
    assert await storage.list("") == [key(thread_id)]


async def test_keys_outside_the_session_are_refused(threads):
    client, made = threads
    thread_id = await bound_thread(client, made, TRAVELER)
    storage = AuroraSnapshotStorage(client, session_id=thread_id, traveler_id=TRAVELER)
    with pytest.raises(StorageError, match="outside workflow session"):
        await storage.write(key("someone-else"), b"{}")
    with pytest.raises(StorageError, match="outside workflow session"):
        await storage.read(key("someone-else"))


async def test_snapshots_cannot_be_deleted_through_the_storage(threads):
    client, made = threads
    thread_id = await bound_thread(client, made, TRAVELER)
    storage = AuroraSnapshotStorage(client, session_id=thread_id, traveler_id=TRAVELER)
    with pytest.raises(StorageError, match="append-only"):
        await storage.delete(key(thread_id))


async def test_a_scoped_reader_sees_only_its_own_bound_snapshots(threads):
    """Negative controls have rows: a decoy's bound thread, and an unbound thread of ours."""
    client, made = threads
    mine = await bound_thread(client, made, TRAVELER)
    decoys = await bound_thread(client, made, DECOY)
    unbound = f"snap-unbound-{uuid.uuid4().hex[:8]}"
    made.append((unbound, None))
    for session_id, traveler_id in ((mine, TRAVELER), (decoys, DECOY), (unbound, TRAVELER)):
        storage = AuroraSnapshotStorage(client, session_id=session_id, traveler_id=traveler_id)
        await storage.write(key(session_id), b'{"data": {"state": {"status": "executing"}}}')

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
