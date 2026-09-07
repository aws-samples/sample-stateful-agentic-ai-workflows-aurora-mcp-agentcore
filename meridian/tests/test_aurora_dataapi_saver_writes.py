"""Pending writes and history for the Data API saver, against live Aurora.

Pending writes are what a resumed graph replays. Losing one, renumbering a
reserved index, or dropping task_path turns a resume into a silent
recomputation, so each is pinned here.

These asserted against a recording fake and checked the SQL text the saver
emitted. That is not the property that matters. What matters is what Aurora
holds afterwards, which is what these assert now: the fake could not have
told you that overwrite semantics differ between reserved and ordinary
channels, because nothing was enforcing a primary key.

Requires migration 007 and AWS credentials.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from langgraph.checkpoint.base import WRITES_IDX_MAP

from backend.db.aurora_dataapi_saver import AuroraDataApiSaver
from backend.db.blob_windows import MAX_ROW_BYTES, split_for_write
from tests.test_aurora_dataapi_saver import Cluster, FaultAfter, _checkpoint, cluster

__all__ = ["cluster"]

pytestmark = pytest.mark.database

RESERVED_CHANNEL = next(iter(WRITES_IDX_MAP))


class TransactionalFault(FaultAfter):
    """`FaultAfter` that also carries the client's transaction API.

    `_write_pending_blob` opens a real RDS Data API transaction, so a proxy
    that fails one statement has to pass begin/commit/rollback through for
    the rollback to be a real rollback in Aurora.
    """

    def begin_transaction(self) -> str:
        return self.client.begin_transaction()

    def commit_transaction(self, transaction_id: str) -> None:
        self.client.commit_transaction(transaction_id)

    def rollback_transaction(self, transaction_id: str) -> None:
        self.client.rollback_transaction(transaction_id)


def _write_config(cluster: Cluster, checkpoint_id: str = "cp1") -> dict:
    return {
        "configurable": {
            "thread_id": cluster.thread_id,
            "checkpoint_ns": "",
            "checkpoint_id": checkpoint_id,
        }
    }


async def _seed_checkpoint(cluster: Cluster) -> None:
    """Pending writes hang off a checkpoint, so one has to exist to read them."""
    await cluster.saver().aput(
        cluster.config(), _checkpoint(), {"step": 1}, {"messages": "1"}
    )


async def _stored_writes(cluster: Cluster) -> list[dict]:
    return await cluster.client.execute(
        """
        SELECT checkpoint_id, task_id, idx, channel, type, task_id, task_path,
               octet_length(blob) AS n
          FROM checkpoint_writes WHERE thread_id = %s ORDER BY idx
        """,
        (cluster.thread_id,),
    )


# ------------------------------------------------------------ the write row


async def test_writes_use_the_full_natural_key(cluster: Cluster) -> None:
    await cluster.saver().aput_writes(
        _write_config(cluster), [("messages", "a")], "task-1", "path/0"
    )
    rows = await _stored_writes(cluster)
    assert len(rows) == 1
    assert rows[0]["checkpoint_id"] == "cp1"
    assert rows[0]["task_id"] == "task-1"
    assert rows[0]["task_path"] == "path/0"
    assert rows[0]["channel"] == "messages"


async def test_task_path_defaults_to_empty_not_null(cluster: Cluster) -> None:
    """The column is NOT NULL upstream; a None would be rejected outright."""
    await cluster.saver().aput_writes(
        _write_config(cluster), [("messages", "a")], "task-1"
    )
    rows = await _stored_writes(cluster)
    assert rows[0]["task_path"] == ""


async def test_indices_are_sequential_from_zero(cluster: Cluster) -> None:
    await cluster.saver().aput_writes(
        _write_config(cluster), [("a", 1), ("b", 2), ("c", 3)], "task-1"
    )
    rows = await _stored_writes(cluster)
    assert [int(r["idx"]) for r in rows] == [0, 1, 2]
    assert [r["channel"] for r in rows] == ["a", "b", "c"]


async def test_reserved_channels_get_their_fixed_negative_index(
    cluster: Cluster,
) -> None:
    await cluster.saver().aput_writes(
        _write_config(cluster), [(RESERVED_CHANNEL, "a")], "task-1"
    )
    rows = await _stored_writes(cluster)
    assert int(rows[0]["idx"]) == WRITES_IDX_MAP[RESERVED_CHANNEL]


# ------------------------------------------------------- overwrite semantics


async def test_a_reserved_write_is_a_latest_value_slot(cluster: Cluster) -> None:
    """A re-put of __resume__ must replace the stored blob, not double it."""
    await _seed_checkpoint(cluster)
    saver = cluster.saver()
    config = _write_config(cluster)
    await saver.aput_writes(config, [(RESERVED_CHANNEL, "first")], "task-1")
    await saver.aput_writes(config, [(RESERVED_CHANNEL, "second")], "task-1")

    _, encoded_second = saver.serde.dumps_typed("second")
    rows = await _stored_writes(cluster)
    assert len(rows) == 1, "a reserved write must not accumulate rows"
    assert int(rows[0]["n"]) == len(encoded_second), (
        "the stored blob is exactly the new value, so it was replaced rather "
        "than concatenated onto the old one"
    )

    replayed = await cluster.reader().aget_tuple(cluster.config())
    values = {channel: value for _task, channel, value in replayed.pending_writes}
    assert values[RESERVED_CHANNEL] == "second"


async def test_an_ordinary_write_is_append_once(cluster: Cluster) -> None:
    """Overwriting an ordinary write silently replaces committed task output."""
    await _seed_checkpoint(cluster)
    saver = cluster.saver()
    config = _write_config(cluster)
    await saver.aput_writes(config, [("messages", "original")], "task-1")
    await saver.aput_writes(config, [("messages", "retried")], "task-1")

    rows = await _stored_writes(cluster)
    assert len(rows) == 1
    replayed = await cluster.reader().aget_tuple(cluster.config())
    values = {channel: value for _task, channel, value in replayed.pending_writes}
    assert values["messages"] == "original", "a retry must not replace the first write"


async def test_a_mixed_batch_does_not_overwrite(cluster: Cluster) -> None:
    await _seed_checkpoint(cluster)
    saver = cluster.saver()
    config = _write_config(cluster)
    batch = [(RESERVED_CHANNEL, "a"), ("messages", "b")]
    await saver.aput_writes(config, batch, "task-1")
    await saver.aput_writes(config, [(RESERVED_CHANNEL, "c"), ("messages", "d")], "task-1")

    replayed = await cluster.reader().aget_tuple(cluster.config())
    values = {channel: value for _task, channel, value in replayed.pending_writes}
    assert values["messages"] == "b", "the ordinary channel is append-once"


def test_reserved_write_reset_sets_rather_than_concatenates() -> None:
    """The reset must be ``blob = EXCLUDED.blob``, not a concatenation.

    Reserved-write idempotence depends on this: a re-put of the same
    ``__resume__`` payload must replace the stored blob, not double it.
    """
    from backend.db.aurora_dataapi_saver import UPSERT_WRITE_SQL

    assert "blob = EXCLUDED.blob" in UPSERT_WRITE_SQL


def test_write_window_sql_casts_both_substring_bounds() -> None:
    """``substring(bytea, bigint, bigint)`` has no overload on PostgreSQL."""
    from backend.db.aurora_dataapi_saver import WRITE_WINDOW_SQL

    assert "FROM %s::integer FOR %s::integer" in WRITE_WINDOW_SQL


# ------------------------------------------------------ segmented durability


async def test_a_large_pending_write_round_trips_byte_identically(
    cluster: Cluster,
) -> None:
    await _seed_checkpoint(cluster)
    saver = cluster.saver()
    payload = "q" * (MAX_ROW_BYTES * 2 + 11)
    await saver.aput_writes(_write_config(cluster), [("messages", payload)], "task-1")

    _, encoded = saver.serde.dumps_typed(payload)
    assert len(split_for_write(encoded)) == 3, "test setup must span three windows"

    replayed = await cluster.reader().aget_tuple(cluster.config())
    values = {channel: value for _task, channel, value in replayed.pending_writes}
    assert values["messages"] == payload


async def test_a_retried_ordinary_batch_does_not_double_the_blob(
    cluster: Cluster,
) -> None:
    """Under DO NOTHING a conflicting insert must skip its appends.

    Appending them anyway would leave a blob twice its true length, which
    deserializes as garbage rather than failing.
    """
    await _seed_checkpoint(cluster)
    saver = cluster.saver()
    config = _write_config(cluster)
    payload = "r" * (MAX_ROW_BYTES * 2)
    await saver.aput_writes(config, [("messages", payload)], "task-1")
    first = (await _stored_writes(cluster))[0]["n"]

    await saver.aput_writes(config, [("messages", payload)], "task-1")
    assert (await _stored_writes(cluster))[0]["n"] == first

    replayed = await cluster.reader().aget_tuple(cluster.config())
    values = {channel: value for _task, channel, value in replayed.pending_writes}
    assert values["messages"] == payload


async def test_an_append_failure_rolls_back_the_whole_pending_write(
    cluster: Cluster,
) -> None:
    """Either every segment lands or none does.

    A truncated row would survive a retry, because the retry's DO NOTHING
    correctly sees the row already exists and skips re-appending it.
    """
    await _seed_checkpoint(cluster)
    saver = AuroraDataApiSaver(
        TransactionalFault(cluster.client, "UPDATE checkpoint_writes SET blob")
    )
    payload = "s" * (MAX_ROW_BYTES * 2)
    with pytest.raises(RuntimeError, match="injected failure"):
        await saver.aput_writes(
            _write_config(cluster), [("messages", payload)], "task-1"
        )
    assert await cluster.rows("checkpoint_writes") == 0, "a partial row must not remain"

    # The retry, against a healthy cluster, builds a complete row.
    await cluster.saver().aput_writes(
        _write_config(cluster), [("messages", payload)], "task-1"
    )
    replayed = await cluster.reader().aget_tuple(cluster.config())
    values = {channel: value for _task, channel, value in replayed.pending_writes}
    assert values["messages"] == payload


async def test_a_truncated_pending_write_is_an_error_not_a_short_value(
    cluster: Cluster,
) -> None:
    await _seed_checkpoint(cluster)
    await cluster.saver().aput_writes(
        _write_config(cluster),
        [("messages", "t" * (MAX_ROW_BYTES * 2))],
        "task-1",
    )
    faulty = AuroraDataApiSaver(
        TransactionalFault(cluster.client, "substring(blob FROM")
    )
    with pytest.raises(RuntimeError):
        await faulty.aget_tuple(cluster.config())


async def test_a_write_row_with_no_type_is_refused(cluster: Cluster) -> None:
    """Reading a blob without its serializer tag would silently corrupt it."""
    await _seed_checkpoint(cluster)
    await cluster.client.execute(
        """
        INSERT INTO checkpoint_writes
            (thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel,
             type, blob, task_path)
        VALUES (%s, '', 'cp1', 'task-1', 0, 'messages', NULL, %s, '')
        """,
        (cluster.thread_id, b"not-decodable"),
    )
    with pytest.raises(RuntimeError, match="no stored type"):
        await cluster.saver().aget_tuple(cluster.config())


# ------------------------------------------------------------- replay shape


async def test_pending_writes_reach_a_resumed_read(cluster: Cluster) -> None:
    saver = cluster.saver()
    await saver.aput(
        cluster.config(), _checkpoint(), {"step": 1}, {"messages": "1"}
    )
    await saver.aput_writes(
        _write_config(cluster), [("messages", "pending")], "task-1", "path/0"
    )
    tup = await cluster.reader().aget_tuple(cluster.config())
    assert tup.pending_writes, "a resumed graph replays these"
    task_id, channel, value = tup.pending_writes[0]
    assert (task_id, channel, value) == ("task-1", "messages", "pending")


# -------------------------------------------------------------------- alist


async def _chain(cluster: Cluster, count: int) -> list[str]:
    """Write `count` checkpoints, oldest first, returning their ids."""
    saver = cluster.saver()
    ids = [f"cp{n:02d}" for n in range(1, count + 1)]
    parent: str | None = None
    for cid in ids:
        config = dict(cluster.config())
        if parent:
            config = {
                "configurable": {
                    **cluster.config()["configurable"],
                    "checkpoint_id": parent,
                }
            }
        await saver.aput(config, _checkpoint(cid), {"step": 1}, {"messages": "1"})
        parent = cid
    return ids


async def test_alist_orders_newest_first(cluster: Cluster) -> None:
    ids = await _chain(cluster, 3)
    seen = [t.checkpoint["id"] async for t in cluster.reader().alist(cluster.config())]
    assert seen == list(reversed(ids))


async def test_alist_pages_with_a_limit(cluster: Cluster) -> None:
    ids = await _chain(cluster, 4)
    seen = [
        t.checkpoint["id"]
        async for t in cluster.reader().alist(cluster.config(), limit=2)
    ]
    assert seen == list(reversed(ids))[:2]


async def test_alist_applies_before_as_keyset_pagination(cluster: Cluster) -> None:
    """History must page without ever returning a full response near 1 MiB."""
    ids = await _chain(cluster, 4)
    before = {
        "configurable": {
            **cluster.config()["configurable"],
            "checkpoint_id": ids[2],
        }
    }
    seen = [
        t.checkpoint["id"]
        async for t in cluster.reader().alist(cluster.config(), before=before)
    ]
    assert seen == list(reversed(ids[:2]))


async def test_alist_carries_parent_config_when_present(cluster: Cluster) -> None:
    ids = await _chain(cluster, 2)
    newest = [t async for t in cluster.reader().alist(cluster.config(), limit=1)][0]
    assert newest.parent_config["configurable"]["checkpoint_id"] == ids[0]


async def test_alist_parent_config_is_none_without_a_parent(
    cluster: Cluster,
) -> None:
    await _chain(cluster, 1)
    only = [t async for t in cluster.reader().alist(cluster.config())][0]
    assert only.parent_config is None


async def test_alist_merges_inline_and_blob_channel_values(
    cluster: Cluster,
) -> None:
    """Same merge as aget_tuple, on the history path."""
    saver = cluster.saver()
    await saver._write_blob(cluster.thread_id, "", "messages", "1", ["msg-1"])
    inlined = {
        "v": 1,
        "id": "cp1",
        "ts": "2026-09-06T00:00:00+00:00",
        "channel_values": {"traveler_id": "TRV-001"},
        "channel_versions": {"messages": "1"},
        "versions_seen": {},
    }
    await cluster.client.execute(
        """
        INSERT INTO checkpoints
            (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id,
             type, checkpoint, metadata)
        VALUES (%s, '', 'cp1', NULL, 'json', %s::JSONB, %s::JSONB)
        """,
        (cluster.thread_id, json.dumps(inlined), json.dumps({"step": 1})),
    )
    only = [t async for t in cluster.reader().alist(cluster.config())][0]
    values = only.checkpoint["channel_values"]
    assert values["traveler_id"] == "TRV-001"
    assert values["messages"] == ["msg-1"]


async def test_alist_yields_pending_writes(cluster: Cluster) -> None:
    saver = cluster.saver()
    await saver.aput(cluster.config(), _checkpoint(), {"step": 1}, {"messages": "1"})
    await saver.aput_writes(
        _write_config(cluster), [("messages", "pending")], "task-1"
    )
    only = [t async for t in cluster.reader().alist(cluster.config())][0]
    assert only.pending_writes == [("task-1", "messages", "pending")]


@pytest.mark.parametrize("limit", [0, -5], ids=["zero", "negative"])
async def test_alist_clamps_a_nonsense_limit_to_one(
    cluster: Cluster, limit: int
) -> None:
    await _chain(cluster, 2)
    seen = [
        t async for t in cluster.reader().alist(cluster.config(), limit=limit)
    ]
    assert len(seen) == 1


async def test_alist_raises_for_a_nonempty_filter(cluster: Cluster) -> None:
    """Metadata filtering is a documented gap, and must fail loudly."""
    with pytest.raises(NotImplementedError):
        [t async for t in cluster.saver().alist(cluster.config(), filter={"step": 1})]


@pytest.mark.parametrize("empty", [None, {}], ids=["none", "empty-dict"])
async def test_alist_accepts_an_absent_filter(cluster: Cluster, empty: Any) -> None:
    await _chain(cluster, 1)
    seen = [t async for t in cluster.reader().alist(cluster.config(), filter=empty)]
    assert len(seen) == 1


async def test_alist_raises_without_a_config(cluster: Cluster) -> None:
    with pytest.raises(ValueError):
        [t async for t in cluster.saver().alist(None)]


async def test_alist_raises_when_config_lacks_thread_id(cluster: Cluster) -> None:
    with pytest.raises(ValueError):
        [t async for t in cluster.saver().alist({"configurable": {}})]
