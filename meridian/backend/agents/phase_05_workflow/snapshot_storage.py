"""Strands workflow snapshots, appended to Aurora as JSONB rows.

Implements the Strands ``Storage`` protocol that ``SnapshotSessionManager``
writes through. Every write appends a row, so the table keeps each node
boundary of a run, and the newest row for a key is what a resume restores.
Rows are stamped with the traveler, execution and worker that trusted code
supplies after the lease claim, never with values read from the snapshot.
"""

import time
from typing import Callable, List, Optional

from strands.types.exceptions import StorageError

INSERT_SQL = """
INSERT INTO workflow_snapshots
    (storage_key, session_id, traveler_id, execution_id, worker_id, snapshot)
VALUES (%s, %s, %s, %s, %s, %s::jsonb)
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
        client: RDS Data API client. Writes run as its role, outside a scoped
            session, as the LangGraph saver did under migration 013.
        session_id: The workflow thread. Keys outside it are refused.
        traveler_id: The traveler the lease claim authorized.
        execution_id: The execution holding the lease, when one does.
        worker_id: The process or Runtime session doing the work.
        on_write: Called with each write's duration in milliseconds.
    """

    def __init__(
        self,
        client,
        *,
        session_id: str,
        traveler_id: str,
        execution_id: Optional[str] = None,
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
        """Append one snapshot row."""
        started = time.perf_counter()
        await self._client.execute(INSERT_SQL, (
            self._owned(key), self._session_id, self._traveler_id,
            self._execution_id, self._worker_id, data.decode("utf-8"),
        ))
        if self._on_write is not None:
            self._on_write(round((time.perf_counter() - started) * 1000))

    async def read(self, key: str) -> Optional[bytes]:
        """Return the newest snapshot for ``key``, or None."""
        rows = await self._client.execute(READ_SQL, (self._owned(key),))
        return rows[0]["snapshot"].encode("utf-8") if rows else None

    async def delete(self, key: str) -> None:
        """Refuse: the table is the run's history."""
        raise StorageError(f"workflow snapshots are append-only; refused to delete {key!r}")

    async def list(self, query: str = "") -> List[str]:
        """Return this session's keys that start with ``query``, sorted."""
        rows = await self._client.execute(LIST_SQL, (self._session_id, _like_prefix(query)))
        return [row["storage_key"] for row in rows]
