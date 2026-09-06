"""A LangGraph checkpoint saver that persists through the RDS Data API.

The demo cluster's writer is not publicly accessible, so ``AsyncPostgresSaver``
cannot open a psycopg connection from a laptop. This saver writes the same
tables over the Data API, which is the transport the rest of the application
already uses.

Storage matches ``langgraph-checkpoint-postgres`` 3.1.2 exactly. The Data API
caps a returned row at 64 KB, so values are written in appended segments and
read through windowed ``substring`` calls.
"""

import json
from typing import Any, Optional

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from backend.db.blob_windows import split_for_write, window_offsets

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

UPSERT_CHECKPOINT_SQL = """
INSERT INTO checkpoints
    (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id,
     type, checkpoint, metadata)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id)
DO UPDATE SET checkpoint = EXCLUDED.checkpoint, metadata = EXCLUDED.metadata
"""

BLOB_SIZE_SQL = """
SELECT octet_length(blob) AS n FROM checkpoint_blobs
 WHERE thread_id = %s AND checkpoint_ns = %s AND channel = %s AND version = %s
"""

BLOB_WINDOW_SQL = """
SELECT substring(blob FROM %s FOR %s) AS part FROM checkpoint_blobs
 WHERE thread_id = %s AND checkpoint_ns = %s AND channel = %s AND version = %s
"""

# Read back separately from BLOB_SIZE_SQL/BLOB_WINDOW_SQL rather than folded
# into them: ``JsonPlusSerializer.dumps_typed`` returns "msgpack" (not
# "json") for the values this saver stores, and ``loads_typed`` needs that
# exact type back or it deserializes garbage. See aget_tuple.
BLOB_TYPE_SQL = """
SELECT type FROM checkpoint_blobs
 WHERE thread_id = %s AND checkpoint_ns = %s AND channel = %s AND version = %s
"""

SELECT_CHECKPOINT_SQL = """
SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id,
       checkpoint, metadata
  FROM checkpoints
 WHERE thread_id = %s AND checkpoint_ns = %s
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
    ) -> Optional[bytes]:
        """Read one channel value in windows under the 64 KB row limit."""
        key = (thread_id, ns, channel, version)
        rows = await self.client.execute(BLOB_SIZE_SQL, key)
        if not rows or rows[0].get("n") is None:
            return None
        total = int(rows[0]["n"])
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
        return b"".join(parts)

    async def _read_blob_type(
        self, thread_id: str, ns: str, channel: str, version: str
    ) -> str:
        """Read the ``type`` a channel value was serialized with.

        ``dumps_typed`` is not guaranteed to return ``"json"`` -- the
        installed ``JsonPlusSerializer`` returns ``"msgpack"`` -- so the
        stored type must be read back rather than assumed.
        """
        rows = await self.client.execute(
            BLOB_TYPE_SQL, (thread_id, ns, channel, version)
        )
        return rows[0]["type"] if rows else "json"

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
        await self.client.execute(
            UPSERT_CHECKPOINT_SQL,
            (
                thread_id,
                ns,
                checkpoint["id"],
                configurable.get("checkpoint_id"),
                "json",
                json.dumps(stored),
                json.dumps(dict(metadata)),
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
        values: dict[str, Any] = {}
        for channel, version in channel_versions.items():
            version = str(version)
            payload = await self._read_blob(thread_id, ns, channel, version)
            if payload is not None:
                blob_type = await self._read_blob_type(thread_id, ns, channel, version)
                values[channel] = self.serde.loads_typed((blob_type, payload))
        return values

    async def aget_tuple(self, config: dict) -> Optional[CheckpointTuple]:
        """Load one checkpoint and rehydrate its channel values."""
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
        checkpoint["channel_values"] = await self._load_channel_values(
            thread_id, ns, checkpoint.get("channel_versions", {})
        )

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
            parent_config=(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": ns,
                        "checkpoint_id": row["parent_checkpoint_id"],
                    }
                }
                if row.get("parent_checkpoint_id")
                else None
            ),
            pending_writes=[],
        )
