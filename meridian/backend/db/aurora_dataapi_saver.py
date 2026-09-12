"""A LangGraph checkpoint saver that persists through the RDS Data API.

The demo cluster's writer is not publicly accessible, so ``AsyncPostgresSaver``
cannot open a psycopg connection from a laptop. This saver writes the same
tables over the Data API, which is the transport the rest of the application
already uses.

The ``checkpoints`` and ``checkpoint_blobs`` tables, their columns, and their
natural keys are identical to ``langgraph-checkpoint-postgres`` 3.1.2. The
write shape differs in one respect: this saver writes every channel value to
``checkpoint_blobs``, while upstream inlines primitive values directly into
the ``checkpoints.checkpoint`` JSONB and only writes a blob row for the rest.
Both directions still read correctly -- upstream's reader (and this saver's)
merges inline ``channel_values`` with blob-backed ones, blobs winning on key
collision -- so a checkpoint written by either saver reads correctly through
the other. The Data API caps a returned row at 64 KB, so values -- in both
``checkpoint_blobs`` and ``checkpoint_writes``, the latter including the
``RESUME`` payload that carries a replayed interrupt answer -- are written in
appended segments and read through windowed ``substring`` calls.
"""

import asyncio
import json
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, Sequence

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_serializable_checkpoint_metadata,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from backend.db.blob_windows import split_for_write, window_offsets


async def _bounded_reads(items: Sequence, read: Callable[[Any], Awaitable[Any]]) -> list:
    """Read independent blobs with bounded fan-out and drain every started read."""
    semaphore = asyncio.Semaphore(4)

    async def limited(item):
        async with semaphore:
            return await read(item)

    results = await asyncio.gather(
        *(limited(item) for item in items), return_exceptions=True
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return results


UPSERT_BLOB_SQL = """
INSERT INTO checkpoint_blobs
    (thread_id, checkpoint_ns, channel, version, type, blob)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (thread_id, checkpoint_ns, channel, version)
DO UPDATE SET type = EXCLUDED.type, blob = EXCLUDED.blob
"""

APPEND_BLOB_SQL = """
UPDATE checkpoint_blobs SET blob = blob || %s
 WHERE thread_id = %s AND checkpoint_ns = %s AND channel = %s AND version = %s
"""

# checkpoint and metadata are JSONB columns. The Data API sends every string
# parameter as text and PostgreSQL will not coerce text into jsonb implicitly,
# so both are cast at the call site.
UPSERT_CHECKPOINT_SQL = """
INSERT INTO checkpoints
    (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id,
     type, checkpoint, metadata)
VALUES (%s, %s, %s, %s, %s, %s::JSONB, %s::JSONB)
ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id)
DO UPDATE SET checkpoint = EXCLUDED.checkpoint, metadata = EXCLUDED.metadata
"""

BLOB_SIZE_SQL = """
SELECT octet_length(blob) AS n, type FROM checkpoint_blobs
 WHERE thread_id = %s AND checkpoint_ns = %s AND channel = %s AND version = %s
"""

BLOB_WINDOW_SQL = """
SELECT substring(blob FROM %s::integer FOR %s::integer) AS part FROM checkpoint_blobs
 WHERE thread_id = %s AND checkpoint_ns = %s AND channel = %s AND version = %s
"""

SELECT_CHECKPOINT_SQL = """
SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id,
       checkpoint, metadata
  FROM checkpoints
 WHERE thread_id = %s AND checkpoint_ns = %s
"""

# Two conflict clauses. The columns, conflict target, and update set match
# langgraph-checkpoint-postgres 3.1.2; the column order does not -- upstream
# orders task_path 5th and idx 6th, this implementation puts task_path last.
# Reserved channels are latest-value slots and must overwrite; ordinary writes
# are append-once and must not, or a retry silently replaces task output.
_WRITE_COLUMNS = """
INSERT INTO checkpoint_writes
    (thread_id, checkpoint_ns, checkpoint_id, task_id, idx,
     channel, type, blob, task_path)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id, task_id, idx) """

# RETURNING lets the caller tell whether this statement actually wrote the
# row -- required so a retried batch knows whether to append its remaining
# segments (see _write_pending_blob).
UPSERT_WRITE_SQL = _WRITE_COLUMNS + """DO UPDATE SET
    channel = EXCLUDED.channel, type = EXCLUDED.type, blob = EXCLUDED.blob
RETURNING 1
"""

INSERT_WRITE_SQL = _WRITE_COLUMNS + "DO NOTHING RETURNING 1"

APPEND_WRITE_SQL = """
UPDATE checkpoint_writes SET blob = blob || %s
 WHERE thread_id = %s AND checkpoint_ns = %s AND checkpoint_id = %s
   AND task_id = %s AND idx = %s
"""

SELECT_WRITES_META_SQL = """
SELECT task_id, idx, channel, type, octet_length(blob) AS n
  FROM checkpoint_writes
 WHERE thread_id = %s AND checkpoint_ns = %s AND checkpoint_id = %s
 ORDER BY task_path, task_id, idx
"""

WRITE_WINDOW_SQL = """
SELECT substring(blob FROM %s::integer FOR %s::integer) AS part
  FROM checkpoint_writes
 WHERE thread_id = %s AND checkpoint_ns = %s AND checkpoint_id = %s
   AND task_id = %s AND idx = %s
"""


class AuroraDataApiSaver(BaseCheckpointSaver):
    """Persist LangGraph checkpoints to Aurora through the RDS Data API."""

    def __init__(self, client: Any, serde: Optional[Any] = None) -> None:
        """Initialize the saver.

        Args:
            client: An ``RDSDataClient``.
            serde: Serializer. Defaults to LangGraph's ``JsonPlusSerializer``
                so values are byte-compatible with the Postgres saver.
        """
        super().__init__(serde=serde or JsonPlusSerializer())
        self.client = client

    async def _write_blob(
        self, thread_id: str, ns: str, channel: str, version: str, value: Any
    ) -> None:
        """Write one channel value, appending beyond the first window."""
        blob_type, payload = self.serde.dumps_typed(value)
        segments = split_for_write(payload)
        await self.client.execute(
            UPSERT_BLOB_SQL,
            (thread_id, ns, channel, version, blob_type, segments[0]),
        )
        for segment in segments[1:]:
            await self.client.execute(
                APPEND_BLOB_SQL, (segment, thread_id, ns, channel, version)
            )

    async def _read_blob(
        self, thread_id: str, ns: str, channel: str, version: str
    ) -> Optional[tuple[str, bytes]]:
        """Read one channel value in windows under the 64 KB row limit.

        Args:
            thread_id: The thread the checkpoint belongs to.
            ns: The checkpoint namespace.
            channel: The channel name.
            version: The channel version string.

        Returns:
            A ``(blob_type, payload)`` pair, or ``None`` if no blob row exists
            for this key.

        Raises:
            RuntimeError: If a blob row disappears mid-read, its stored
                ``type`` is missing or empty, or the reassembled payload does
                not match the size read up front.
        """
        key = (thread_id, ns, channel, version)
        rows = await self.client.execute(BLOB_SIZE_SQL, key)
        if not rows or rows[0].get("n") is None:
            return None
        total = int(rows[0]["n"])
        blob_type = rows[0].get("type")
        if not blob_type:
            raise RuntimeError(
                f"checkpoint blob has no stored type: thread={thread_id} "
                f"channel={channel} version={version}"
            )
        parts: list[bytes] = []
        for offset, length in window_offsets(total):
            window = await self.client.execute(
                BLOB_WINDOW_SQL, (offset, length) + key
            )
            if not window:
                raise RuntimeError(
                    f"checkpoint blob vanished mid-read: thread={thread_id} "
                    f"channel={channel} version={version} offset={offset}"
                )
            parts.append(window[0]["part"])
        payload = b"".join(parts)
        if len(payload) != total:
            raise RuntimeError(
                f"checkpoint blob reassembled to the wrong length: "
                f"thread={thread_id} channel={channel} version={version} "
                f"expected={total} got={len(payload)}"
            )
        return blob_type, payload

    async def aput(
        self,
        config: dict,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> dict:
        """Persist a checkpoint, blobs first.

        The ``checkpoints`` row is what makes a checkpoint visible, so it is
        written last. A failed blob write leaves nothing to resume from rather
        than something that cannot be read back.

        Args:
            config: The config the checkpoint is being saved under. Its
                ``configurable`` block supplies ``thread_id``,
                ``checkpoint_ns`` and the parent ``checkpoint_id``.
            checkpoint: The checkpoint to persist, including its
                ``channel_values``.
            metadata: Metadata associated with the checkpoint.
            new_versions: The channel versions written by this checkpoint.

        Returns:
            The config to retrieve this checkpoint, with ``checkpoint_id``
            set to this checkpoint's id.
        """
        configurable = config["configurable"]
        thread_id = configurable["thread_id"]
        ns = configurable.get("checkpoint_ns", "")
        values = checkpoint.get("channel_values", {})

        for channel, version in new_versions.items():
            if channel in values:
                await self._write_blob(
                    thread_id, ns, channel, str(version), values[channel]
                )

        stored = {k: v for k, v in checkpoint.items() if k != "channel_values"}
        serializable_metadata = get_serializable_checkpoint_metadata(config, metadata)
        await self.client.execute(
            UPSERT_CHECKPOINT_SQL,
            (
                thread_id,
                ns,
                checkpoint["id"],
                configurable.get("checkpoint_id"),
                "json",
                json.dumps(stored),
                json.dumps(serializable_metadata),
            ),
        )
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    async def _load_channel_values(
        self, thread_id: str, ns: str, channel_versions: dict
    ) -> dict[str, Any]:
        """Rehydrate every channel value named by a checkpoint's versions."""
        async def read(item):
            channel, version = item
            found = await self._read_blob(thread_id, ns, channel, str(version))
            if found is not None:
                blob_type, payload = found
                return channel, self.serde.loads_typed((blob_type, payload))
            return None

        # These are immutable versioned blobs read outside a transaction.
        # Keep writes and individual segmented-blob reads sequential.
        loaded = await _bounded_reads(list(channel_versions.items()), read)
        return dict(item for item in loaded if item is not None)

    async def aget_tuple(self, config: dict) -> Optional[CheckpointTuple]:
        """Load one checkpoint and rehydrate its channel values.

        Primitive channel values may already be inlined in the stored
        ``checkpoint`` JSONB -- upstream's ``AsyncPostgresSaver`` writes them
        that way instead of to ``checkpoint_blobs``. Blob-backed values are
        merged on top of whatever is already inline, winning on key
        collision, so a checkpoint written by either saver reads correctly.

        Args:
            config: The config identifying the checkpoint to load.
                ``configurable`` must supply ``thread_id`` and may supply
                ``checkpoint_ns`` and ``checkpoint_id``; the latest
                checkpoint for the thread is returned when ``checkpoint_id``
                is omitted.

        Returns:
            The matching ``CheckpointTuple``, or ``None`` if no checkpoint
            exists for the given config.
        """
        configurable = config["configurable"]
        thread_id = configurable["thread_id"]
        ns = configurable.get("checkpoint_ns", "")
        checkpoint_id = configurable.get("checkpoint_id")

        sql = SELECT_CHECKPOINT_SQL
        params: tuple = (thread_id, ns)
        if checkpoint_id:
            sql += " AND checkpoint_id = %s"
            params += (checkpoint_id,)
        else:
            sql += " ORDER BY checkpoint_id DESC LIMIT 1"

        rows = await self.client.execute(sql, params)
        if not rows:
            return None

        row = rows[0]
        checkpoint = json.loads(row["checkpoint"])
        checkpoint["channel_values"] = {
            **(checkpoint.get("channel_values") or {}),
            **await self._load_channel_values(
                thread_id, ns, checkpoint.get("channel_versions", {})
            ),
        }

        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": ns,
                    "checkpoint_id": row["checkpoint_id"],
                }
            },
            checkpoint=checkpoint,
            metadata=json.loads(row["metadata"]),
            parent_config=self._parent_config(
                thread_id, ns, row.get("parent_checkpoint_id")
            ),
            pending_writes=await self._pending_writes(
                thread_id, ns, row["checkpoint_id"]
            ),
        )

    def _parent_config(
        self, thread_id: str, ns: str, parent_checkpoint_id: Optional[str]
    ) -> Optional[dict]:
        """Build a checkpoint row's parent config.

        Args:
            thread_id: The thread the checkpoint belongs to.
            ns: The checkpoint namespace.
            parent_checkpoint_id: The row's ``parent_checkpoint_id`` column.

        Returns:
            The parent's config, or ``None`` if the row has no parent.
        """
        if not parent_checkpoint_id:
            return None
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "checkpoint_id": parent_checkpoint_id,
            }
        }

    async def aput_writes(
        self,
        config: dict,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """Persist pending writes for a task.

        Reserved channels carry fixed negative indices from ``WRITES_IDX_MAP``
        and are latest-value slots, so a batch made entirely of them upserts.
        Any other batch inserts without overwriting, because an ordinary write
        is append-once and replacing it on retry loses task output. This is the
        same branch ``AsyncPostgresSaver`` takes.

        Args:
            config: Carries thread_id, checkpoint_ns and checkpoint_id.
            writes: (channel, value) pairs in emission order.
            task_id: The task that produced them.
            task_path: Position in the task tree; part of write ordering.
        """
        from langgraph.checkpoint.base import WRITES_IDX_MAP

        configurable = config["configurable"]
        thread_id = configurable["thread_id"]
        ns = configurable.get("checkpoint_ns", "")
        checkpoint_id = configurable["checkpoint_id"]

        sql = (
            UPSERT_WRITE_SQL
            if all(channel in WRITES_IDX_MAP for channel, _ in writes)
            else INSERT_WRITE_SQL
        )

        for offset, (channel, value) in enumerate(writes):
            idx = WRITES_IDX_MAP.get(channel, offset)
            blob_type, payload = self.serde.dumps_typed(value)
            segments = split_for_write(payload)
            await self._write_pending_blob(
                sql,
                (thread_id, ns, checkpoint_id, task_id, idx),
                (
                    thread_id,
                    ns,
                    checkpoint_id,
                    task_id,
                    idx,
                    channel,
                    blob_type,
                    segments[0],
                    task_path,
                ),
                segments,
            )

    async def _write_pending_blob(
        self, sql: str, key: tuple, row_params: tuple, segments: list
    ) -> None:
        """Insert the first segment, appending the rest only if it was written.

        Under ``DO NOTHING``, a conflict means some earlier attempt already
        wrote this row in full, so ``RETURNING`` yields no row and the
        remaining segments are skipped -- appending them again would double
        the stored blob. Under ``DO UPDATE`` the row is always (re)written in
        full, so ``RETURNING`` always yields a row and the remaining
        segments are always appended.

        The insert and its appends run inside one RDS Data API transaction.
        Without it, each ``execute`` auto-commits on its own: a process death
        after the insert lands but before the appends would leave the blob
        truncated with no repair path, because a retry's ``DO NOTHING``
        correctly sees the row already exists and skips re-appending. Wrapping
        both in one transaction means either every segment lands or the whole
        attempt rolls back, leaving a clean row for the next retry to build on.
        It also serializes concurrent re-puts of a reserved channel (e.g.
        ``__resume__``): the row lock held for the transaction's lifetime
        keeps one attempt's ``DO UPDATE`` reset from interleaving with
        another's appends.

        Args:
            sql: ``UPSERT_WRITE_SQL`` or ``INSERT_WRITE_SQL``, both ending in
                ``RETURNING`` so the caller can tell whether this statement
                actually wrote a row.
            key: The write's natural key -- ``(thread_id, checkpoint_ns,
                checkpoint_id, task_id, idx)`` -- used to target the append.
            row_params: Parameters for the first-segment insert statement.
            segments: All segments for this value; ``segments[0]`` is already
                in ``row_params``, and only ``segments[1:]`` are appended.

        Raises:
            Exception: Whatever the insert, an append, or the transaction
                itself raises, after rolling back so no partial row survives.
        """
        transaction_id = self.client.begin_transaction()
        try:
            rows = await self.client.execute(
                sql, row_params, transaction_id=transaction_id
            )
            if rows:
                for segment in segments[1:]:
                    await self.client.execute(
                        APPEND_WRITE_SQL, (segment,) + key, transaction_id=transaction_id
                    )
            self.client.commit_transaction(transaction_id)
        except BaseException:
            # Cancellation is a BaseException. A cancelled worker must release
            # this transaction too, or takeover can block on its row lock.
            self.client.rollback_transaction(transaction_id)
            raise

    async def _pending_writes(
        self, thread_id: str, ns: str, checkpoint_id: str
    ) -> list[tuple[str, str, Any]]:
        """Load pending writes in their stored order, windowed under the row cap.

        Raises:
            RuntimeError: If a pending-write row's stored ``type`` is missing
                or empty, or its size is unexpectedly ``NULL``.
        """
        rows = await self.client.execute(
            SELECT_WRITES_META_SQL, (thread_id, ns, checkpoint_id)
        )
        async def read(row):
            channel = row["channel"]
            blob_type = row.get("type")
            if not blob_type:
                raise RuntimeError(
                    f"pending write has no stored type: thread={thread_id} "
                    f"checkpoint={checkpoint_id} task={row['task_id']} channel={channel}"
                )
            if row.get("n") is None:
                raise RuntimeError(
                    f"pending write has no stored size: thread={thread_id} "
                    f"checkpoint={checkpoint_id} task={row['task_id']} channel={channel}"
                )
            key = (thread_id, ns, checkpoint_id, row["task_id"], row["idx"])
            payload = await self._read_write_blob(key, channel, int(row["n"]))
            return row["task_id"], channel, self.serde.loads_typed((blob_type, payload))

        # gather preserves the metadata query's order, including reserved indices.
        return await _bounded_reads(rows, read)

    async def _read_write_blob(self, key: tuple, channel: str, total: int) -> bytes:
        """Reassemble one pending-write blob from windowed ``substring`` reads.

        Args:
            key: ``(thread_id, checkpoint_ns, checkpoint_id, task_id, idx)``.
            channel: The write's channel name, for diagnostics only.
            total: The blob's size from ``octet_length(blob)``.

        Returns:
            The reassembled payload.

        Raises:
            RuntimeError: If a window vanishes mid-read, or the reassembled
                payload does not match the size read up front.
        """
        thread_id, ns, checkpoint_id, task_id, _idx = key
        parts: list[bytes] = []
        for offset, length in window_offsets(total):
            window = await self.client.execute(WRITE_WINDOW_SQL, (offset, length) + key)
            if not window:
                raise RuntimeError(
                    f"pending write vanished mid-read: thread={thread_id} "
                    f"checkpoint={checkpoint_id} task={task_id} channel={channel} "
                    f"offset={offset}"
                )
            parts.append(window[0]["part"])
        payload = b"".join(parts)
        if len(payload) != total:
            raise RuntimeError(
                f"pending write reassembled to the wrong length: "
                f"thread={thread_id} checkpoint={checkpoint_id} task={task_id} "
                f"channel={channel} expected={total} got={len(payload)}"
            )
        return payload

    async def alist(
        self,
        config: Optional[dict],
        *,
        filter: Optional[dict] = None,
        before: Optional[dict] = None,
        limit: Optional[int] = None,
    ) -> AsyncIterator[CheckpointTuple]:
        """Yield checkpoints for a thread, newest first.

        Pages with a bounded LIMIT and keyset pagination on checkpoint_id so a
        response never approaches the Data API's 1 MiB ceiling.

        Args:
            config: Must supply ``configurable.thread_id``; ``checkpoint_ns``
                defaults to ``""``.
            filter: Metadata filtering. Not implemented -- must be ``None``
                or empty; no caller passes this today.
            before: A config whose ``configurable.checkpoint_id`` bounds the
                page from above, for keyset pagination.
            limit: Maximum checkpoints to yield. Defaults to 50 when omitted
                and is clamped to the range [1, 50] otherwise.

        Yields:
            Matching ``CheckpointTuple`` instances, newest first.

        Raises:
            ValueError: If ``config`` is missing or has no ``thread_id``.
            NotImplementedError: If ``filter`` is non-empty.
        """
        if filter:
            raise NotImplementedError(
                "AuroraDataApiSaver.alist does not implement metadata "
                "filtering; pass filter=None"
            )
        configurable = (config or {}).get("configurable", {})
        thread_id = configurable.get("thread_id")
        if not thread_id:
            raise ValueError(
                "alist requires config['configurable']['thread_id']"
            )
        ns = configurable.get("checkpoint_ns", "")

        sql = SELECT_CHECKPOINT_SQL
        params: tuple = (thread_id, ns)
        if before:
            sql += " AND checkpoint_id < %s"
            params += (before["configurable"]["checkpoint_id"],)
        sql += " ORDER BY checkpoint_id DESC LIMIT %s"
        page_limit = 50 if limit is None else max(1, min(limit, 50))
        params += (page_limit,)

        for row in await self.client.execute(sql, params):
            checkpoint = json.loads(row["checkpoint"])
            # Same merge as aget_tuple: upstream inlines primitives in the
            # JSONB and writes no blob row for them, so both sources count.
            checkpoint["channel_values"] = {
                **(checkpoint.get("channel_values") or {}),
                **await self._load_channel_values(
                    thread_id, ns, checkpoint.get("channel_versions", {})
                ),
            }
            yield CheckpointTuple(
                config={
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": ns,
                        "checkpoint_id": row["checkpoint_id"],
                    }
                },
                checkpoint=checkpoint,
                metadata=json.loads(row["metadata"]),
                parent_config=self._parent_config(
                    thread_id, ns, row.get("parent_checkpoint_id")
                ),
                pending_writes=await self._pending_writes(
                    thread_id, ns, row["checkpoint_id"]
                ),
            )
