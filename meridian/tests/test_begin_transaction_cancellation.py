"""A task cancelled while BeginTransaction is in flight still rolls that transaction back."""

import asyncio
import threading

import pytest

from backend.agents.phase_05_workflow.snapshot_storage import AuroraSnapshotStorage
from backend.db.rds_data_client import RDSDataClient

KEY = "session/t1/scopes/multiAgent/phase5/snapshots/snapshot_latest.json"
WAIT_SECONDS = 5


class SlowBeginBoto:
    """A boto client whose BeginTransaction waits on ``release`` and records every call."""

    def __init__(self, fail_begin: bool = False) -> None:
        self.started, self.release = threading.Event(), threading.Event()
        self.fail_begin = fail_begin
        self.calls: list[tuple[str, str | None]] = []

    def begin_transaction(self, **_kwargs):
        self.started.set()
        assert self.release.wait(WAIT_SECONDS), "the test never released BeginTransaction"
        if self.fail_begin:
            raise RuntimeError("BeginTransaction failed")
        self.calls.append(("begin", None))
        return {"transactionId": "tx-begun"}

    def commit_transaction(self, **kwargs):
        self.calls.append(("commit", kwargs["transactionId"]))

    def rollback_transaction(self, **kwargs):
        self.calls.append(("rollback", kwargs["transactionId"]))

    def execute_statement(self, **kwargs):
        self.calls.append(("statement", kwargs.get("transactionId")))
        return {"records": [], "columnMetadata": []}


def data_client(boto) -> RDSDataClient:
    client = RDSDataClient(
        cluster_arn="arn:c", secret_arn="arn:s", database="d", region="us-east-1"
    )
    client.client = boto
    return client


async def cancel_while_begin_is_in_flight(open_transaction, boto) -> None:
    """Cancel ``open_transaction`` once BeginTransaction started, then let the call finish."""
    task = asyncio.create_task(open_transaction())
    assert await asyncio.to_thread(boto.started.wait, WAIT_SECONDS), "begin never started"
    task.cancel()
    await asyncio.sleep(0)
    boto.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_scoped_session_cancelled_during_begin_rolls_back_the_begun_transaction():
    boto = SlowBeginBoto()

    async def open_session():
        async with data_client(boto).scoped_session(traveler_id=None):
            pytest.fail("the body must not run after a cancellation during begin")

    await cancel_while_begin_is_in_flight(open_session, boto)

    assert boto.calls == [("begin", None), ("rollback", "tx-begun")]


async def test_a_snapshot_write_cancelled_during_begin_rolls_back_the_begun_transaction():
    boto = SlowBeginBoto()
    storage = AuroraSnapshotStorage(
        data_client(boto), session_id="t1", traveler_id="trv", execution_id="exe-1"
    )

    await cancel_while_begin_is_in_flight(lambda: storage.write(KEY, b"{}"), boto)

    assert boto.calls == [("begin", None), ("rollback", "tx-begun")]


async def test_a_begin_that_fails_after_the_cancellation_has_nothing_to_roll_back():
    boto = SlowBeginBoto(fail_begin=True)

    async def open_session():
        async with data_client(boto).scoped_session(traveler_id=None):
            pytest.fail("the body must not run")

    await cancel_while_begin_is_in_flight(open_session, boto)

    assert boto.calls == []


async def test_a_failed_rollback_after_the_cancellation_is_logged_and_the_cancellation_stands(
        caplog):
    boto = SlowBeginBoto()

    def broken_rollback(**_kwargs):
        raise RuntimeError("rollback refused")

    boto.rollback_transaction = broken_rollback

    async def open_session():
        async with data_client(boto).scoped_session(traveler_id=None):
            pytest.fail("the body must not run")

    await cancel_while_begin_is_in_flight(open_session, boto)

    assert any("tx-begun" in record.getMessage() for record in caplog.records)
