"""An in-memory stand-in for the RDS Data API, for the conformance suite.

Models ``checkpoints``, ``checkpoint_blobs``, and ``checkpoint_writes`` (per
``scripts/migrations/007_journey_shell.sql``) as dicts keyed by their natural
keys, and interprets ``AuroraDataApiSaver``'s exact SQL constants --
``octet_length``/``substring`` windowing, ``blob || %s`` appends,
``ON CONFLICT ... DO NOTHING``/``DO UPDATE ... RETURNING``, ordering, and
``LIMIT`` -- with real PostgreSQL semantics instead of canned results. A test
double, not a database: it recognizes only statements the saver issues.
"""

import copy
from typing import Optional

from backend.db.aurora_dataapi_saver import (
    APPEND_BLOB_SQL,
    APPEND_WRITE_SQL,
    BLOB_SIZE_SQL,
    BLOB_WINDOW_SQL,
    INSERT_WRITE_SQL,
    SELECT_CHECKPOINT_SQL,
    SELECT_WRITES_META_SQL,
    UPSERT_BLOB_SQL,
    UPSERT_CHECKPOINT_SQL,
    UPSERT_WRITE_SQL,
    WRITE_WINDOW_SQL,
)


def _norm(sql: str) -> str:
    """Collapse whitespace so multi-line SQL constants compare reliably."""
    return " ".join(sql.split())


_SELECT_CHECKPOINT_BASE = _norm(SELECT_CHECKPOINT_SQL)


class FakeCluster:
    """An in-memory double for an ``RDSDataClient``, keyed by natural key."""

    def __init__(self) -> None:
        self.checkpoints: dict[tuple, dict] = {}
        self.blobs: dict[tuple, dict] = {}
        self.writes: dict[tuple, dict] = {}
        self._tx_snapshots: dict[str, tuple] = {}
        self._next_tx = 0

    def begin_transaction(self) -> str:
        """Start a transaction, snapshotting all tables for a possible rollback."""
        self._next_tx += 1
        transaction_id = f"tx-{self._next_tx}"
        self._tx_snapshots[transaction_id] = (
            copy.deepcopy(self.checkpoints),
            copy.deepcopy(self.blobs),
            copy.deepcopy(self.writes),
        )
        return transaction_id

    def commit_transaction(self, transaction_id: str) -> None:
        """Discard the rollback snapshot; writes are already applied."""
        self._tx_snapshots.pop(transaction_id, None)

    def rollback_transaction(self, transaction_id: str) -> None:
        """Restore all tables to their state at ``begin_transaction``."""
        snapshot = self._tx_snapshots.pop(transaction_id, None)
        if snapshot is not None:
            self.checkpoints, self.blobs, self.writes = snapshot

    async def execute(
        self, sql: str, params: tuple = (), *, transaction_id: Optional[str] = None
    ) -> list[dict]:
        """Dispatch one of the saver's fixed SQL statements to its handler.

        ``transaction_id`` is unused (writes apply immediately; rollback
        restores the begin-time snapshot). Raises ``NotImplementedError``
        for any other statement.
        """
        del transaction_id
        normalized = _norm(sql)
        params = tuple(params)
        if normalized == _norm(UPSERT_BLOB_SQL):
            return self._upsert_blob(params)
        if normalized == _norm(APPEND_BLOB_SQL):
            return self._append(self.blobs, params)
        if normalized == _norm(BLOB_SIZE_SQL):
            return self._blob_size(params)
        if normalized == _norm(BLOB_WINDOW_SQL):
            return self._window(self.blobs, params)
        if normalized == _norm(UPSERT_CHECKPOINT_SQL):
            return self._upsert_checkpoint(params)
        if normalized.startswith(_SELECT_CHECKPOINT_BASE):
            return self._select_checkpoints(normalized, params)
        if normalized == _norm(UPSERT_WRITE_SQL):
            return self._put_write(params, overwrite=True)
        if normalized == _norm(INSERT_WRITE_SQL):
            return self._put_write(params, overwrite=False)
        if normalized == _norm(APPEND_WRITE_SQL):
            return self._append(self.writes, params)
        if normalized == _norm(SELECT_WRITES_META_SQL):
            return self._select_writes_meta(params)
        if normalized == _norm(WRITE_WINDOW_SQL):
            return self._window(self.writes, params)
        raise NotImplementedError(f"FakeCluster cannot handle: {normalized[:100]}")

    @staticmethod
    def _append(table: dict, params: tuple) -> list[dict]:
        """Apply a ``blob = blob || %s`` append to whichever table owns the key."""
        segment, *key = params
        table[tuple(key)]["blob"] += segment
        return []

    @staticmethod
    def _window(table: dict, params: tuple) -> list[dict]:
        """Apply a ``substring(blob FROM %s::integer FOR %s::integer)`` read."""
        offset, length, *key = params
        row = table.get(tuple(key))
        if row is None:
            return []
        start = offset - 1
        return [{"part": row["blob"][start : start + length]}]

    def _upsert_blob(self, params: tuple) -> list[dict]:
        thread_id, ns, channel, version, blob_type, blob = params
        self.blobs[(thread_id, ns, channel, version)] = {"type": blob_type, "blob": blob}
        return []

    def _blob_size(self, params: tuple) -> list[dict]:
        row = self.blobs.get(params)
        return [] if row is None else [{"n": len(row["blob"]), "type": row["type"]}]

    def _upsert_checkpoint(self, params: tuple) -> list[dict]:
        thread_id, ns, cp_id, parent_id, type_, checkpoint_json, metadata_json = params
        key = (thread_id, ns, cp_id)
        existing = self.checkpoints.get(key)
        if existing is None:
            self.checkpoints[key] = {
                "thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": cp_id,
                "parent_checkpoint_id": parent_id, "type": type_,
                "checkpoint": checkpoint_json, "metadata": metadata_json,
            }
        else:
            existing["checkpoint"] = checkpoint_json
            existing["metadata"] = metadata_json
        return []

    def _select_checkpoints(self, normalized: str, params: tuple) -> list[dict]:
        thread_id, ns = params[0], params[1]
        rest = params[2:]
        rows = [
            r for r in self.checkpoints.values()
            if r["thread_id"] == thread_id and r["checkpoint_ns"] == ns
        ]
        if "checkpoint_id = %s" in normalized:
            rows = [r for r in rows if r["checkpoint_id"] == rest[0]]
            rest = rest[1:]
        elif "checkpoint_id < %s" in normalized:
            rows = [r for r in rows if r["checkpoint_id"] < rest[0]]
            rest = rest[1:]
        rows.sort(key=lambda r: r["checkpoint_id"], reverse=True)
        if "LIMIT %s" in normalized:
            rows = rows[: rest[0]]
        elif normalized.endswith("LIMIT 1"):
            rows = rows[:1]
        return [dict(r) for r in rows]

    def _put_write(self, params: tuple, *, overwrite: bool) -> list[dict]:
        """Handle the ``DO UPDATE``/``DO NOTHING`` writes, both ending ``RETURNING 1``."""
        thread_id, ns, cp_id, task_id, idx, channel, type_, blob, task_path = params
        key = (thread_id, ns, cp_id, task_id, idx)
        existing = self.writes.get(key)
        if existing is None:
            self.writes[key] = {
                "channel": channel, "type": type_, "blob": blob, "task_path": task_path
            }
            return [{"?column?": 1}]
        if not overwrite:
            return []
        existing.update(channel=channel, type=type_, blob=blob)
        return [{"?column?": 1}]

    def _select_writes_meta(self, params: tuple) -> list[dict]:
        thread_id, ns, cp_id = params
        matches = [
            (key, row) for key, row in self.writes.items()
            if key[0] == thread_id and key[1] == ns and key[2] == cp_id
        ]
        matches.sort(key=lambda item: (item[1]["task_path"], item[0][3], item[0][4]))
        return [
            {
                "task_id": key[3], "idx": key[4], "channel": row["channel"],
                "type": row["type"], "n": len(row["blob"]),
            }
            for key, row in matches
        ]
