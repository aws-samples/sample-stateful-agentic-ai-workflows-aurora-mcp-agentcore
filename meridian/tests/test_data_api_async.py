"""Slow Data API statements must not starve the worker lease heartbeat."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from backend.db.rds_data_client import RDSDataClient


@pytest.mark.parametrize("cancellations", [0, 1, 2])
async def test_statement_yields_and_cancellation_drains_inflight_io(cancellations):
    entered, release = threading.Event(), threading.Event()
    finished = []

    def execute_statement(**kwargs):
        entered.set()
        if not release.wait(timeout=3):
            raise AssertionError("The statement blocked the event loop")
        finished.append(kwargs["transactionId"])
        return {"columnMetadata": [{"name": "n"}], "records": [[{"longValue": 1}]]}

    client = RDSDataClient.__new__(RDSDataClient)
    client.client = SimpleNamespace(execute_statement=execute_statement)
    client.cluster_arn, client.secret_arn, client.database = "cluster", "secret", "database"
    task = asyncio.create_task(client.execute("SELECT 1", transaction_id="tx"))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        # Reaching here while the SDK call is in flight proves another task,
        # such as lease renewal, can run. A cancelled caller must still wait
        # for this request before it can roll back that same transaction.
        for _ in range(cancellations):
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        release.set()
        if cancellations:
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert await task == [{"n": 1}]
        assert finished == ["tx"]
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
