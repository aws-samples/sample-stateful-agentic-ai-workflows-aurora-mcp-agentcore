"""AuroraSnapshotStorage ends every transaction it begins, whatever the statement does."""

import asyncio

import pytest
from botocore.exceptions import ClientError
from strands.types.exceptions import StorageError

from backend.agents.phase_05_workflow.snapshot_storage import (
    PIN_TRAVELER_SQL,
    AuroraSnapshotStorage,
)
from backend.db.journey_store import ExecutionLeaseLostError

KEY = "session/t1/scopes/multiAgent/phase5/snapshots/snapshot_latest.json"


class RecordingClient:
    """Records the transaction calls; the statement's outcome is set per test."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls: list[str] = []

    def begin_transaction(self) -> str:
        self.calls.append("begin")
        return "tx-1"

    def commit_transaction(self, tx: str) -> None:
        self.calls.append(f"commit {tx}")

    def rollback_transaction(self, tx: str) -> None:
        self.calls.append(f"rollback {tx}")

    async def execute(self, sql, params=(), transaction_id=None):
        assert transaction_id == "tx-1"
        if sql == PIN_TRAVELER_SQL:
            self.calls.append("pin")
            return []
        self.calls.append("statement")
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def storage(client) -> AuroraSnapshotStorage:
    return AuroraSnapshotStorage(client, session_id="t1", traveler_id="trv", execution_id="exe-1")


async def test_a_successful_write_commits_once():
    client = RecordingClient([{"snapshot_seq": 1}])
    await storage(client).write(KEY, b"{}")
    assert client.calls == ["begin", "pin", "statement", "commit tx-1"]


async def test_an_empty_fenced_insert_rolls_back_and_raises():
    client = RecordingClient([])
    with pytest.raises(ExecutionLeaseLostError):
        await storage(client).write(KEY, b"{}")
    assert client.calls == ["begin", "pin", "statement", "rollback tx-1"]


async def test_a_client_error_rolls_back_and_raises_storage_error():
    error = ClientError({"Error": {"Code": "DatabaseErrorException"}}, "ExecuteStatement")
    client = RecordingClient(error)
    with pytest.raises(StorageError):
        await storage(client).read(KEY)
    assert client.calls == ["begin", "pin", "statement", "rollback tx-1"]


async def test_cancellation_during_the_statement_rolls_back_and_propagates():
    client = RecordingClient(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await storage(client).write(KEY, b"{}")
    assert client.calls == ["begin", "pin", "statement", "rollback tx-1"]
