"""Strands workflow snapshots, appended to Aurora as JSONB rows.

Implements the Strands ``Storage`` protocol that ``SnapshotSessionManager``
writes through. Every write appends a row, so the table keeps each node
boundary of a run, and the newest row for a key is what a resume restores.
Rows are stamped with the traveler, execution and worker that trusted code
supplies after the lease claim, never with values read from the snapshot.
Each read and write pins that traveler in its own transaction, so the row-level
security policies decide what the Runtime's login sees and appends.
A write lands only while its execution is still the thread's running one, so a
worker that stalled past its lease cannot append an older state after another
worker took the thread over.
"""

import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable, List, Optional

from botocore.exceptions import ClientError
from strands.types.exceptions import StorageError

from backend.db.journey_store import ExecutionLeaseLostError

# The Data API refuses a result over 1 MB, so a snapshot this cap admits must still read back.
# Live tests read back ASCII, multibyte and escape-dense snapshots just under this cap.
MAX_SNAPSHOT_BYTES = 900_000

PIN_TRAVELER_SQL = "SELECT set_config('app.current_traveler_id', %s, true)"

INSERT_SQL = """
INSERT INTO workflow_snapshots
    (storage_key, session_id, traveler_id, execution_id, worker_id, snapshot)
SELECT %s, %s, %s, %s, %s, %s::jsonb
 WHERE EXISTS (
     SELECT 1 FROM journey_executions
      WHERE execution_id = %s AND thread_id = %s AND status = 'running'
 )
RETURNING snapshot_seq
"""
READ_SQL = """
SELECT snapshot::TEXT AS snapshot FROM workflow_snapshots
 WHERE storage_key = %s ORDER BY snapshot_seq DESC LIMIT 1
"""
LIST_SQL = """
SELECT DISTINCT storage_key FROM workflow_snapshots
 WHERE session_id = %s AND storage_key LIKE %s ESCAPE '\\'
 ORDER BY storage_key
"""


def _like_prefix(prefix: str) -> str:
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%"


class AuroraSnapshotStorage:
    """One workflow session's snapshots in ``workflow_snapshots``.

    Args:
        client: The Data API client; in the Runtime its secret is meridian_workflow's.
            Reads and writes pin the traveler in their own transaction.
        session_id: The workflow thread. Keys outside it are refused.
        traveler_id: The traveler the lease claim authorized.
        execution_id: The execution holding the lease. None makes the storage
            read-only: a write is refused, because nothing fences it.
        worker_id: The process or Runtime session doing the work.
        on_write: Called with each write's duration in milliseconds. The time covers
            begin, traveler pin, statement and commit.
    """

    def __init__(
        self,
        client,
        *,
        session_id: str,
        traveler_id: str,
        execution_id: Optional[str],
        worker_id: Optional[str] = None,
        on_write: Optional[Callable[[int], None]] = None,
    ) -> None:
        self._client = client
        self._session_id = session_id
        self._traveler_id = traveler_id
        self._execution_id = execution_id
        self._worker_id = worker_id
        self._on_write = on_write

    def _owned(self, key: str) -> str:
        parts = key.split("/")
        if len(parts) < 3 or parts[0] != "session" or parts[1] != self._session_id:
            raise StorageError(f"Key {key!r} is outside workflow session {self._session_id}")
        return key

    async def write(self, key: str, data: bytes) -> None:
        """Append one snapshot row while this execution still holds the thread.

        Raises:
            StorageError: The storage has no execution, the key is outside the session,
                the snapshot is over ``MAX_SNAPSHOT_BYTES``, or Aurora refused the write.
            ExecutionLeaseLostError: The execution no longer runs the thread; nothing was written.
        """
        if self._execution_id is None:
            raise StorageError(
                f"read-only storage for workflow session {self._session_id}: "
                "bind an execution before writing"
            )
        if len(data) > MAX_SNAPSHOT_BYTES:
            raise StorageError(
                f"snapshot for {key!r} is {len(data)} bytes; the Data API refuses a result "
                f"over 1 MB, so the cap is {MAX_SNAPSHOT_BYTES} bytes. "
                "Reduce what the nodes return."
            )
        owned = self._owned(key)
        started = time.perf_counter()
        try:
            async with self._pinned() as tx:
                rows = await self._client.execute(INSERT_SQL, (
                    owned, self._session_id, self._traveler_id,
                    self._execution_id, self._worker_id, data.decode("utf-8"),
                    self._execution_id, self._session_id,
                ), transaction_id=tx)
                if not rows:
                    raise ExecutionLeaseLostError(
                        f"Execution {self._execution_id} no longer runs thread "
                        f"{self._session_id}; its snapshot was not saved. "
                        "Re-read the saved journey."
                    )
        except ClientError as exc:
            raise StorageError(
                f"writing {owned!r} failed: {exc.response['Error']['Code']}"
            ) from exc
        if self._on_write is not None:
            self._on_write(round((time.perf_counter() - started) * 1000))

    async def read(self, key: str) -> Optional[bytes]:
        """Return the newest snapshot for ``key``, or None.

        Raises:
            StorageError: The key is outside the session, or Aurora refused the read.
        """
        owned = self._owned(key)
        try:
            async with self._pinned() as tx:
                rows = await self._client.execute(READ_SQL, (owned,), transaction_id=tx)
        except ClientError as exc:
            raise StorageError(
                f"reading {owned!r} failed: {exc.response['Error']['Code']}"
            ) from exc
        return rows[0]["snapshot"].encode("utf-8") if rows else None

    @asynccontextmanager
    async def _pinned(self) -> AsyncIterator[str]:
        """A transaction in which RLS sees this storage's traveler.

        Snapshot statements skip scoped_session's grant check and audit row on
        purpose: the lease claim already ran both for this run, and an audit row
        per snapshot would bury the authorization evidence. The master role
        ignores the pin; meridian_workflow is bound by it.
        """
        tx = self._client.begin_transaction()
        try:
            await self._client.execute(PIN_TRAVELER_SQL, (self._traveler_id,), transaction_id=tx)
            yield tx
        except BaseException:
            self._client.rollback_transaction(tx)
            raise
        self._client.commit_transaction(tx)

    async def delete(self, key: str) -> None:
        """Refuse: the table is the run's history."""
        raise StorageError(f"workflow snapshots are append-only; refused to delete {key!r}")

    async def list(self, query: str = "") -> List[str]:
        """Return this session's keys that start with ``query``, sorted.

        Raises:
            StorageError: Aurora refused the read.
        """
        try:
            async with self._pinned() as tx:
                rows = await self._client.execute(
                    LIST_SQL, (self._session_id, _like_prefix(query)), transaction_id=tx)
        except ClientError as exc:
            raise StorageError(
                f"listing {query!r} failed: {exc.response['Error']['Code']}"
            ) from exc
        return [row["storage_key"] for row in rows]
