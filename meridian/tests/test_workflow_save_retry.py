"""A snapshot write is retried as a unit, and a failed save never strands or unholds a run."""

import json
import time

import pytest
from botocore.exceptions import ClientError, ConnectionClosedError, ReadTimeoutError
from strands.storage import InMemoryStorage
from strands.types.exceptions import StorageError

from backend.agents.phase_05_workflow import snapshot_storage as storage_module
from backend.agents.phase_05_workflow.finished import snapshot_finished
from backend.agents.phase_05_workflow.graph import next_nodes, snapshot_key
from backend.agents.phase_05_workflow.runner import WorkflowConflictError
from backend.agents.phase_05_workflow.snapshot_storage import (
    INSERT_SQL,
    LIST_SQL,
    PIN_TRAVELER_SQL,
    READ_SQL,
    AuroraSnapshotStorage,
)
from backend.db.journey_store import ExecutionLeaseLostError
from tests.test_workflow_runner import RECOVERY, World, command

KEY = "session/t1/scopes/multiAgent/phase5/snapshots/snapshot_latest.json"


def throttle() -> ClientError:
    return ClientError({"Error": {"Code": "ThrottlingException"}}, "ExecuteStatement")


class FakeDataApi:
    """A Data API client over memory: transactions, the fenced insert, newest-row reads.

    ``faults`` holds (kind, predicate, error) triples consumed in order. A kind is
    ``insert`` (raise on the INSERT), ``commit`` (raise on commit before it lands) or
    ``lost_reply`` (the commit lands, then raises, as a dropped response would).
    """

    def __init__(self):
        self.rows: list[dict] = []
        self.calls: list[str] = []
        self.faults: list[tuple] = []
        self.fence_open = True
        self._pending: dict[str, list[dict]] = {}
        self._next = 0

    def fail(self, kind, error, when=lambda data: True, times=1):
        self.faults.extend([(kind, when, error)] * times)

    def _take(self, kind, data=None):
        for fault in self.faults:
            if fault[0] == kind and fault[1](data):
                self.faults.remove(fault)
                raise fault[2]

    def begin_transaction(self) -> str:
        self._next += 1
        tx = f"tx-{self._next}"
        self._pending[tx] = []
        self.calls.append("begin")
        return tx

    def commit_transaction(self, tx: str) -> None:
        pending = self._pending.pop(tx)
        landed = any(f[0] == "lost_reply" for f in self.faults)
        if not landed:
            self._take("commit")
        self.rows.extend(pending)
        self.calls.append("commit")
        if landed:
            self._take("lost_reply")

    def rollback_transaction(self, tx: str) -> None:
        self._pending.pop(tx, None)
        self.calls.append("rollback")

    async def execute(self, sql, params=(), transaction_id=None):
        if sql == PIN_TRAVELER_SQL:
            return []
        if sql == INSERT_SQL:
            self.calls.append("insert")
            data = params[-1]
            self._take("insert", data)
            if not self.fence_open:
                return []
            self._pending[transaction_id].append(
                {"storage_key": params[2], "execution_id": params[0], "snapshot": data}
            )
            return [{"snapshot_seq": len(self.rows) + 1}]
        if sql == READ_SQL:
            self.calls.append("read")
            mine = [r for r in self.rows if r["storage_key"] == params[0]]
            return [{"snapshot": mine[-1]["snapshot"]}] if mine else []
        if sql == LIST_SQL:
            return []
        self.calls.append("newest")
        mine = [r for r in self.rows if r["storage_key"] == params[0]]
        return [mine[-1]] if mine else []


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch):
    monkeypatch.setattr(storage_module, "WRITE_RETRY_DELAYS", (0.0, 0.0, 0.0))


def aurora(client, execution_id="exe-1") -> AuroraSnapshotStorage:
    return AuroraSnapshotStorage(
        client, session_id="t1", traveler_id="trv", execution_id=execution_id, worker_id="w"
    )


async def test_a_throttled_write_is_retried_as_a_whole_transaction():
    client = FakeDataApi()
    client.fail("insert", throttle())
    await aurora(client).write(KEY, b'{"a": 1}')
    assert client.calls == ["begin", "insert", "rollback", "begin", "newest", "insert", "commit"]
    assert len(client.rows) == 1


async def test_a_dropped_connection_on_commit_is_retried():
    client = FakeDataApi()
    client.fail("commit", ConnectionClosedError(endpoint_url="https://x"))
    await aurora(client).write(KEY, b'{"a": 1}')
    assert len(client.rows) == 1


async def test_a_commit_that_landed_but_lost_its_reply_does_not_leave_a_duplicate_row():
    client = FakeDataApi()
    client.fail("lost_reply", ReadTimeoutError(endpoint_url="https://x"))
    await aurora(client).write(KEY, b'{"a": 1}')
    assert len(client.rows) == 1
    await aurora(client).write(KEY, b'{"a": 2}')
    assert len(client.rows) == 2


async def test_a_fence_refusal_raises_at_once_without_a_retry():
    client = FakeDataApi()
    client.fence_open = False
    with pytest.raises(ExecutionLeaseLostError):
        await aurora(client).write(KEY, b"{}")
    assert client.calls.count("begin") == 1


async def test_a_fence_refusal_on_a_retry_attempt_still_raises():
    client = FakeDataApi()
    client.fail("insert", throttle())
    real_take = client._take

    def close_fence_after_first_failure(kind, data=None):
        try:
            real_take(kind, data)
        finally:
            client.fence_open = False

    client._take = close_fence_after_first_failure
    with pytest.raises(ExecutionLeaseLostError):
        await aurora(client).write(KEY, b"{}")
    assert client.rows == []


async def test_an_error_that_is_not_transient_is_not_retried():
    client = FakeDataApi()
    client.fail("insert", ClientError({"Error": {"Code": "DatabaseErrorException"}}, "Execute"))
    with pytest.raises(StorageError, match="DatabaseErrorException"):
        await aurora(client).write(KEY, b"{}")
    assert client.calls.count("begin") == 1


async def test_a_write_that_keeps_failing_gives_up_after_its_attempts():
    client = FakeDataApi()
    client.fail("insert", throttle(), times=10)
    with pytest.raises(StorageError, match="ThrottlingException"):
        await aurora(client).write(KEY, b"{}")
    assert client.calls.count("begin") == 4
    assert client.rows == []


async def test_the_retry_budget_stops_further_attempts(monkeypatch):
    monkeypatch.setattr(storage_module, "WRITE_RETRY_DELAYS", (0.2, 0.2, 0.2))
    monkeypatch.setattr(storage_module, "WRITE_RETRY_BUDGET_SECONDS", 0.3)
    client = FakeDataApi()
    client.fail("insert", throttle(), times=10)
    started = time.monotonic()
    with pytest.raises(StorageError):
        await aurora(client).write(KEY, b"{}")
    assert client.calls.count("begin") == 2
    assert time.monotonic() - started < 1.0


class AuroraWorld(World):
    """A World whose snapshots live in AuroraSnapshotStorage over the fake Data API."""

    def __init__(self):
        super().__init__()
        self.client = FakeDataApi()

    def storage_for(self, thread_id, traveler_id, execution_id, worker_id, on_write):
        return AuroraSnapshotStorage(
            self.client, session_id=thread_id, traveler_id=traveler_id,
            execution_id=execution_id or "exe-read", worker_id=worker_id, on_write=on_write,
        )

    async def snapshot(self, thread):
        raw = await self.storage_for(thread, "trv_x", None, "w", lambda _ms: None).read(
            snapshot_key(thread)
        )
        return raw


def _state(data):
    return json.loads(data)["data"]["state"]


async def test_a_throttled_save_after_the_hold_keeps_the_hold_and_the_run_completes():
    world = AuroraWorld()
    await world.runner().run(command(RECOVERY))
    world.client.fail("insert", throttle(), when=lambda d: "hold" in _state(d)["completed_nodes"])
    result = await world.runner("worker-b").run(command(RECOVERY, resume=True))
    assert result["workflow_status"] == "resumed"
    assert world.gateway.releases == []
    assert [r["status"] for r in world.gateway.receipts.values()] == ["held"]


class FailFinalWrite(InMemoryStorage):
    """Raises on every write that records the invocation-end status ``completed``."""

    async def write(self, key, data):
        if json.loads(data)["data"]["state"]["status"] == "completed":
            raise StorageError("writing failed: ThrottlingException")
        await super().write(key, data)


async def test_a_failed_final_save_after_synthesize_still_completes_without_releasing_the_hold():
    world = World()
    world.storages["t1"] = FailFinalWrite()
    await world.runner().run(command(RECOVERY))
    result = await world.runner("worker-b").run(command(RECOVERY, resume=True))
    assert result["workflow_status"] == "resumed"
    assert world.gateway.releases == []
    assert world.released_statuses("t1")[-1] == "succeeded"


async def test_the_thread_of_a_failed_final_save_reads_as_finished_not_stranded():
    world = World()
    world.storages["t1"] = FailFinalWrite()
    await world.runner().run(command(RECOVERY))
    await world.runner("worker-b").run(command(RECOVERY, resume=True))
    newest = json.loads(await world.snapshot("t1"))
    assert newest["data"]["state"]["status"] != "completed"
    assert next_nodes(newest) == []
    assert snapshot_finished(newest)
    with pytest.raises(WorkflowConflictError, match="already finished"):
        await world.runner("worker-c").run(command(RECOVERY, resume=True))


async def test_a_failed_save_before_synthesize_still_gives_the_hold_back():
    world = World()

    class FailHoldSave(InMemoryStorage):
        async def write(self, key, data):
            if "hold" in _state(data)["completed_nodes"]:
                raise StorageError("writing failed: ThrottlingException")
            await super().write(key, data)

    world.storages["t1"] = FailHoldSave()
    await world.runner().run(command(RECOVERY))
    with pytest.raises(StorageError):
        await world.runner("worker-b").run(command(RECOVERY, resume=True))
    assert len(world.gateway.releases) == 1

