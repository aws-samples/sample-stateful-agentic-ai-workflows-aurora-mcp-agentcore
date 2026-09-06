"""AuroraDataApiSaver writes and reads LangGraph checkpoints over the Data API.

These asserted against a recording fake, which proved the saver emitted the
statements it meant to emit and nothing about whether Aurora would accept
them. It did not: the fake had no column types, so it took a text parameter
for the JSONB `checkpoint` column and the suite stayed green while the saver
could not persist anything. Everything here runs against the live cluster.

Crash safety is the one thing a healthy cluster cannot demonstrate on its
own, so `FaultAfter` wraps the real client and fails one named statement.
Every other statement still goes to Aurora, and the assertion is about what
the cluster holds afterwards.

Requires migration 007 and AWS credentials.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator

import pytest
import pytest_asyncio

from backend.db.aurora_dataapi_saver import AuroraDataApiSaver
from backend.db.blob_windows import MAX_ROW_BYTES, split_for_write
from backend.db.rds_data_client import get_rds_data_client

CHECKPOINT_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")


class FaultAfter:
    """The real cluster, with one statement made to fail.

    Not a stand-in for Aurora: every statement but the named one is executed
    against it. This exists because "a partial write is never visible" cannot
    be observed unless a write is made to fail.
    """

    def __init__(self, client: Any, fail_on: str) -> None:
        self.client = client
        self.fail_on = fail_on

    async def execute(self, sql: str, params: tuple = (), **kwargs: Any) -> Any:
        if self.fail_on in sql:
            raise RuntimeError(f"injected failure on {self.fail_on}")
        return await self.client.execute(sql, params, **kwargs)


async def _purge(client, thread_id: str) -> None:
    for table in CHECKPOINT_TABLES:
        await client.execute(f"DELETE FROM {table} WHERE thread_id = %s", (thread_id,))


class Cluster:
    """The live cluster plus a thread id unique to one test."""

    def __init__(self, client) -> None:
        self.client = client
        self.thread_id = f"itest-saver-{uuid.uuid4().hex[:10]}"

    def config(self, ns: str = "") -> dict:
        return {"configurable": {"thread_id": self.thread_id, "checkpoint_ns": ns}}

    def saver(self) -> AuroraDataApiSaver:
        return AuroraDataApiSaver(self.client)

    def reader(self) -> AuroraDataApiSaver:
        """A saver on its own client, the way a restarted worker reads."""
        return AuroraDataApiSaver(get_rds_data_client())

    async def rows(self, table: str) -> int:
        result = await self.client.execute(
            f"SELECT count(*) AS n FROM {table} WHERE thread_id = %s",
            (self.thread_id,),
        )
        return int(result[0]["n"])


@pytest_asyncio.fixture
async def cluster() -> AsyncIterator[Cluster]:
    handle = Cluster(get_rds_data_client())
    try:
        yield handle
    finally:
        await _purge(handle.client, handle.thread_id)


def _checkpoint(cid: str = "cp1", value: Any = None) -> dict:
    return {
        "v": 1,
        "id": cid,
        "ts": "2026-09-06T00:00:00+00:00",
        "channel_values": {"messages": value if value is not None else ["hello"]},
        "channel_versions": {"messages": "1"},
        "versions_seen": {},
    }


# -------------------------------------------------------------------- aput


async def test_aput_returns_the_new_config(cluster: Cluster) -> None:
    result = await cluster.saver().aput(
        cluster.config(), _checkpoint(), {"step": 1}, {"messages": "1"}
    )
    assert result["configurable"]["checkpoint_id"] == "cp1"
    assert result["configurable"]["thread_id"] == cluster.thread_id


async def test_aput_writes_a_checkpoint_aurora_can_read_back(
    cluster: Cluster,
) -> None:
    """The JSONB columns must actually accept what the saver sends."""
    await cluster.saver().aput(
        cluster.config(), _checkpoint(), {"step": 1}, {"messages": "1"}
    )
    rows = await cluster.client.execute(
        """
        SELECT type, checkpoint ->> 'id' AS id, metadata ->> 'step' AS step
          FROM checkpoints WHERE thread_id = %s
        """,
        (cluster.thread_id,),
    )
    assert rows[0]["id"] == "cp1"
    assert rows[0]["step"] == "1"
    assert rows[0]["type"] == "json"


async def test_a_failed_blob_leaves_no_visible_checkpoint(cluster: Cluster) -> None:
    """A checkpoint that cannot be read back must never be reported as saved."""
    saver = AuroraDataApiSaver(FaultAfter(cluster.client, "checkpoint_blobs"))
    with pytest.raises(RuntimeError, match="injected failure"):
        await saver.aput(
            cluster.config(), _checkpoint(), {"step": 1}, {"messages": "1"}
        )
    assert await cluster.rows("checkpoints") == 0


async def test_a_large_value_is_stored_whole(cluster: Cluster) -> None:
    """Beyond one window the saver appends, and Aurora holds every byte."""
    value = ["x" * (MAX_ROW_BYTES * 2)]
    await cluster.saver().aput(
        cluster.config(), _checkpoint(value=value), {"step": 1}, {"messages": "1"}
    )
    _, expected = cluster.saver().serde.dumps_typed(value)
    assert len(split_for_write(expected)) > 1, "test setup must exceed one window"

    stored = await cluster.client.execute(
        "SELECT octet_length(blob) AS n FROM checkpoint_blobs WHERE thread_id = %s",
        (cluster.thread_id,),
    )
    assert int(stored[0]["n"]) == len(expected)


async def test_a_segmented_write_reassembles_byte_for_byte(cluster: Cluster) -> None:
    value = ["x" * (MAX_ROW_BYTES * 2 + 5)]
    saver = cluster.saver()
    _, expected = saver.serde.dumps_typed(value)
    assert len(split_for_write(expected)) == 3, "test setup must produce three segments"

    await saver._write_blob(cluster.thread_id, "", "messages", "1", value)

    blob_type, payload = await cluster.reader()._read_blob(
        cluster.thread_id, "", "messages", "1"
    )
    assert payload == expected
    assert blob_type


# --------------------------------------------------------------- blob reads


async def test_a_value_below_one_window_round_trips(cluster: Cluster) -> None:
    saver = cluster.saver()
    await saver._write_blob(cluster.thread_id, "", "messages", "1", ["hello"])
    blob_type, payload = await saver._read_blob(cluster.thread_id, "", "messages", "1")
    assert saver.serde.loads_typed((blob_type, payload)) == ["hello"]


@pytest.mark.parametrize(
    "size",
    [MAX_ROW_BYTES - 1, MAX_ROW_BYTES, MAX_ROW_BYTES + 1, MAX_ROW_BYTES * 2 + 7],
    ids=["just-under", "exact", "just-over", "several-windows"],
)
async def test_values_around_the_window_boundary_round_trip(
    cluster: Cluster, size: int
) -> None:
    """Off-by-one in the window arithmetic corrupts resumes rather than erroring."""
    saver = cluster.saver()
    value = "y" * size
    await saver._write_blob(cluster.thread_id, "", "messages", "1", value)
    blob_type, payload = await cluster.reader()._read_blob(
        cluster.thread_id, "", "messages", "1"
    )
    assert saver.serde.loads_typed((blob_type, payload)) == value


async def test_binary_survives_the_full_byte_range(cluster: Cluster) -> None:
    """Embedded nulls and high bytes must not be mangled or double encoded."""
    saver = cluster.saver()
    value = bytes(range(256)) * 400
    await saver._write_blob(cluster.thread_id, "", "messages", "1", value)
    blob_type, payload = await cluster.reader()._read_blob(
        cluster.thread_id, "", "messages", "1"
    )
    assert saver.serde.loads_typed((blob_type, payload)) == value


async def test_read_blob_returns_none_when_absent(cluster: Cluster) -> None:
    assert await cluster.saver()._read_blob(cluster.thread_id, "", "nope", "1") is None


def test_blob_window_sql_casts_both_substring_bounds() -> None:
    """A bigint-typed offset or length makes ``substring(bytea, ...)`` fail

    to resolve on PostgreSQL: there is no ``substring(bytea, bigint, bigint)``
    overload, and ``RDSDataClient`` encodes Python ``int`` as ``longValue``
    (bigint). Both bounds must be cast to ``integer`` explicitly in the SQL.
    """
    from backend.db.aurora_dataapi_saver import BLOB_WINDOW_SQL

    assert "FROM %s::integer FOR %s::integer" in BLOB_WINDOW_SQL


async def test_a_truncated_read_is_an_error_not_a_short_value(
    cluster: Cluster,
) -> None:
    """Silent truncation would resume from a corrupted state."""
    saver = cluster.saver()
    await saver._write_blob(
        cluster.thread_id, "", "messages", "1", ["z" * (MAX_ROW_BYTES * 2)]
    )
    faulty = AuroraDataApiSaver(FaultAfter(cluster.client, "substring(blob FROM"))
    with pytest.raises(RuntimeError):
        await faulty._read_blob(cluster.thread_id, "", "messages", "1")


# ---------------------------------------------------------------- aget_tuple


async def test_aget_tuple_returns_none_for_an_unknown_thread(
    cluster: Cluster,
) -> None:
    missing = {"configurable": {"thread_id": "never-written", "checkpoint_ns": ""}}
    assert await cluster.saver().aget_tuple(missing) is None


async def test_aput_round_trips_through_a_freshly_built_reader(
    cluster: Cluster,
) -> None:
    """The whole point: a different process reads what this one committed."""
    await cluster.saver().aput(
        cluster.config(), _checkpoint(), {"step": 1, "source": "loop"}, {"messages": "1"}
    )
    tup = await cluster.reader().aget_tuple(cluster.config())
    assert tup is not None
    assert tup.checkpoint["id"] == "cp1"
    assert tup.checkpoint["channel_values"] == {"messages": ["hello"]}
    assert tup.metadata["source"] == "loop"


async def test_aget_tuple_merges_inline_and_blob_channel_values(
    cluster: Cluster,
) -> None:
    """Upstream ``AsyncPostgresSaver`` inlines primitive channel values into

    the ``checkpoints.checkpoint`` JSONB and writes no ``checkpoint_blobs``
    row for them. ``aget_tuple`` must merge those inline values with any
    blob-backed channels rather than discarding one or the other. Our own
    ``aput`` never inlines, so the row is written to Aurora the way the
    upstream saver would write it.
    """
    saver = cluster.saver()
    await saver._write_blob(cluster.thread_id, "", "messages", "1", ["msg-1"])

    inlined = {
        "v": 1,
        "id": "cp1",
        "ts": "2026-09-06T00:00:00+00:00",
        "channel_values": {
            "traveler_id": "TRV-001",
            "workflow_status": "interrupted",
        },
        "channel_versions": {"messages": "1"},
        "versions_seen": {},
    }
    await cluster.client.execute(
        """
        INSERT INTO checkpoints
            (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id,
             type, checkpoint, metadata)
        VALUES (%s, %s, %s, NULL, 'json', %s::JSONB, %s::JSONB)
        """,
        (cluster.thread_id, "", "cp1", json.dumps(inlined), json.dumps({"step": 1})),
    )

    tup = await cluster.reader().aget_tuple(cluster.config())
    assert tup is not None
    values = tup.checkpoint["channel_values"]
    assert values["traveler_id"] == "TRV-001"
    assert values["workflow_status"] == "interrupted"
    assert values["messages"] == ["msg-1"]


async def test_the_stored_blob_type_is_used_rather_than_a_literal(
    cluster: Cluster,
) -> None:
    """Reading with the wrong serializer tag corrupts the value silently."""
    saver = cluster.saver()
    await saver.aput(
        cluster.config(), _checkpoint(value={"a": 1}), {"step": 1}, {"messages": "1"}
    )
    stored = await cluster.client.execute(
        "SELECT type FROM checkpoint_blobs WHERE thread_id = %s AND channel = %s",
        (cluster.thread_id, "messages"),
    )
    expected_type, _ = saver.serde.dumps_typed({"a": 1})
    assert stored[0]["type"] == expected_type

    tup = await cluster.reader().aget_tuple(cluster.config())
    assert tup.checkpoint["channel_values"]["messages"] == {"a": 1}


async def test_metadata_is_sanitized_the_way_the_postgres_family_does(
    cluster: Cluster,
) -> None:
    """`writes` is popped before JSONB storage, matching PostgresSaver."""
    await cluster.saver().aput(
        cluster.config(),
        _checkpoint(),
        {"step": 1, "source": "loop", "writes": {"should": "not persist"}},
        {"messages": "1"},
    )
    rows = await cluster.client.execute(
        "SELECT metadata FROM checkpoints WHERE thread_id = %s", (cluster.thread_id,)
    )
    assert "should" not in str(rows[0]["metadata"])
