"""The Data API client keeps the event loop free and never re-sends a statement."""

import asyncio
import time
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError
from strands.types.exceptions import StorageError

from backend.agents.phase_05_workflow.snapshot_storage import AuroraSnapshotStorage
from backend.db import rds_data_client
from backend.db.rds_data_client import RDSDataClient

KEY = "session/t1/scopes/multiAgent/phase5/snapshots/snapshot_latest.json"


class SlowBoto:
    """A boto client whose transaction calls block like a slow Data API."""

    def __init__(self, delay: float = 0.3) -> None:
        self.delay = delay
        self.calls: list[str] = []

    def begin_transaction(self, **kwargs):
        time.sleep(self.delay)
        self.calls.append("begin")
        return {"transactionId": "tx-1"}

    def commit_transaction(self, **kwargs):
        time.sleep(self.delay)
        self.calls.append("commit")

    def rollback_transaction(self, **kwargs):
        self.calls.append("rollback")

    def execute_statement(self, **kwargs):
        self.calls.append("statement")
        return {}


def data_client(boto) -> RDSDataClient:
    client = RDSDataClient(
        cluster_arn="arn:c", secret_arn="arn:s", database="d", region="us-east-1"
    )
    client.client = boto
    return client


def test_the_built_boto_client_does_not_retry_and_has_a_read_timeout():
    config = RDSDataClient(region="us-east-1").client.meta.config
    assert config.retries["total_max_attempts"] == 1
    assert config.retries["mode"] == "standard"
    assert config.read_timeout == 60


async def test_opening_a_scoped_session_does_not_block_the_event_loop():
    client = data_client(SlowBoto())
    ticks = 0

    async def ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    task = asyncio.create_task(ticker())
    async with client.scoped_session(traveler_id=None) as tx:
        assert tx == "tx-1"
        during_begin = ticks
    task.cancel()
    assert during_begin >= 5


async def test_a_failed_commit_in_a_scoped_session_rolls_back():
    boto = SlowBoto(delay=0)
    error = ClientError({"Error": {"Code": "ThrottlingException"}}, "CommitTransaction")
    boto.commit_transaction = MagicMock(side_effect=error)
    with pytest.raises(ClientError):
        async with data_client(boto).scoped_session(traveler_id=None):
            pass
    assert "rollback" in boto.calls


async def test_a_failed_snapshot_commit_rolls_back_once_and_raises_storage_error():
    boto = SlowBoto(delay=0)
    error = ClientError({"Error": {"Code": "ThrottlingException"}}, "CommitTransaction")
    boto.commit_transaction = MagicMock(side_effect=error)
    rollbacks = MagicMock(side_effect=boto.rollback_transaction)
    boto.rollback_transaction = rollbacks
    storage = AuroraSnapshotStorage(
        data_client(boto), session_id="t1", traveler_id="trv", execution_id="exe-1"
    )
    boto.execute_statement = lambda **kwargs: {
        "columnMetadata": [{"name": "snapshot_seq"}], "records": [[{"longValue": 1}]]
    }
    with pytest.raises(StorageError):
        await storage.write(KEY, b"{}")
    assert rollbacks.call_count == 1


def throttle(operation: str) -> ClientError:
    return ClientError({"Error": {"Code": "ThrottlingException"}}, operation)


@pytest.fixture
def no_backoff(monkeypatch):
    monkeypatch.setattr(rds_data_client, "DATA_API_RETRY_BACKOFF_SECONDS", (0, 0))


async def test_a_transactionless_execute_retries_a_throttle(no_backoff):
    boto = SlowBoto(delay=0)
    outcomes = [throttle("ExecuteStatement"), {}]
    boto.execute_statement = MagicMock(side_effect=outcomes)
    assert await data_client(boto).execute("SELECT 1") == []
    assert boto.execute_statement.call_count == 2


async def test_a_transactionless_execute_gives_up_after_three_attempts(no_backoff):
    boto = SlowBoto(delay=0)
    boto.execute_statement = MagicMock(side_effect=throttle("ExecuteStatement"))
    with pytest.raises(ClientError):
        await data_client(boto).execute("SELECT 1")
    assert boto.execute_statement.call_count == 3


async def test_an_execute_inside_a_transaction_never_retries(no_backoff):
    boto = SlowBoto(delay=0)
    boto.execute_statement = MagicMock(side_effect=throttle("ExecuteStatement"))
    with pytest.raises(ClientError):
        await data_client(boto).execute("SELECT 1", transaction_id="tx-1")
    assert boto.execute_statement.call_count == 1


async def test_a_commit_never_retries(no_backoff):
    boto = SlowBoto(delay=0)
    boto.commit_transaction = MagicMock(side_effect=throttle("CommitTransaction"))
    with pytest.raises(ClientError):
        async with data_client(boto).scoped_session(traveler_id=None):
            pass
    assert boto.commit_transaction.call_count == 1


async def test_begin_transaction_retries_a_throttle(no_backoff):
    boto = SlowBoto(delay=0)
    boto.begin_transaction = MagicMock(
        side_effect=[throttle("BeginTransaction"), {"transactionId": "tx-2"}]
    )
    assert data_client(boto).begin_transaction() == "tx-2"
