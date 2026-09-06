"""Pending writes and history for the Data API saver.

Pending writes are what a resumed graph replays. Losing one, renumbering a
reserved index, or dropping task_path turns a resume into a silent
recomputation, so each is pinned here.
"""

import json

import pytest

from backend.db.aurora_dataapi_saver import AuroraDataApiSaver
from backend.db.blob_windows import MAX_ROW_BYTES, split_for_write
from tests.test_aurora_dataapi_saver import FakeDataClient, _checkpoint, _config


@pytest.fixture
def client() -> FakeDataClient:
    return FakeDataClient()


@pytest.fixture
def saver(client: FakeDataClient) -> AuroraDataApiSaver:
    return AuroraDataApiSaver(client)


@pytest.mark.asyncio
async def test_writes_use_the_full_natural_key(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    config = {"configurable": {"thread_id": "t1", "checkpoint_ns": "", "checkpoint_id": "cp1"}}
    await saver.aput_writes(config, [("messages", "a")], "task-1", "path/0")
    sql, params = client.calls[0]
    assert "checkpoint_writes" in sql
    assert "t1" in params and "cp1" in params and "task-1" in params
    assert "path/0" in params


@pytest.mark.asyncio
async def test_task_path_defaults_to_empty_not_null(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    config = {"configurable": {"thread_id": "t1", "checkpoint_ns": "", "checkpoint_id": "cp1"}}
    await saver.aput_writes(config, [("messages", "a")], "task-1")
    _, params = client.calls[0]
    assert "" in params
    assert None not in params


@pytest.mark.asyncio
async def test_indices_are_sequential_from_zero(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    config = {"configurable": {"thread_id": "t1", "checkpoint_ns": "", "checkpoint_id": "cp1"}}
    await saver.aput_writes(config, [("a", 1), ("b", 2), ("c", 3)], "task-1")
    indices = [p[4] for _, p in client.calls]
    assert indices == [0, 1, 2]


@pytest.mark.asyncio
async def test_reserved_channels_get_their_fixed_negative_index(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    from langgraph.checkpoint.base import WRITES_IDX_MAP

    config = {"configurable": {"thread_id": "t1", "checkpoint_ns": "", "checkpoint_id": "cp1"}}
    channel = next(iter(WRITES_IDX_MAP))
    await saver.aput_writes(config, [(channel, "a")], "task-1")
    assert client.calls[0][1][4] == WRITES_IDX_MAP[channel]


@pytest.mark.asyncio
async def test_ordinary_channels_keep_positional_indices(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    config = {"configurable": {"thread_id": "t1", "checkpoint_ns": "", "checkpoint_id": "cp1"}}
    await saver.aput_writes(config, [("a", 1), ("b", 2)], "task-1")
    assert [p[4] for _, p in client.calls] == [0, 1]


@pytest.mark.asyncio
async def test_reserved_only_batches_overwrite(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    """A reserved write is a latest-value slot, so it must upsert."""
    from langgraph.checkpoint.base import WRITES_IDX_MAP

    config = {"configurable": {"thread_id": "t1", "checkpoint_ns": "", "checkpoint_id": "cp1"}}
    channel = next(iter(WRITES_IDX_MAP))
    await saver.aput_writes(config, [(channel, "a")], "task-1")
    assert "DO UPDATE" in client.statements()[0]


@pytest.mark.asyncio
async def test_ordinary_batches_do_not_overwrite(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    """Ordinary writes are append-once. Overwriting them loses task output."""
    config = {"configurable": {"thread_id": "t1", "checkpoint_ns": "", "checkpoint_id": "cp1"}}
    await saver.aput_writes(config, [("messages", "a")], "task-1")
    sql = client.statements()[0]
    assert "ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)" in sql
    assert "DO NOTHING" in sql
    assert "DO UPDATE" not in sql


@pytest.mark.asyncio
async def test_mixed_batches_do_not_overwrite(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    from langgraph.checkpoint.base import WRITES_IDX_MAP

    config = {"configurable": {"thread_id": "t1", "checkpoint_ns": "", "checkpoint_id": "cp1"}}
    channel = next(iter(WRITES_IDX_MAP))
    await saver.aput_writes(config, [(channel, "a"), ("messages", "b")], "task-1")
    assert "DO NOTHING" in client.statements()[0]


@pytest.mark.asyncio
async def test_alist_pages_with_a_limit(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    client.results = [[]]
    [item async for item in saver.alist(_config())]
    assert "LIMIT" in client.statements()[0]


@pytest.mark.asyncio
async def test_alist_orders_newest_first(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    client.results = [[]]
    [item async for item in saver.alist(_config())]
    assert "ORDER BY checkpoint_id DESC" in client.statements()[0]


@pytest.mark.asyncio
async def test_alist_applies_before_as_keyset_pagination(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    client.results = [[]]
    before = {"configurable": {"checkpoint_id": "cp5"}}
    [item async for item in saver.alist(_config(), before=before)]
    sql, params = client.calls[0]
    assert "checkpoint_id <" in sql
    assert "cp5" in params


# --- Important 1: pending-write blobs windowed like checkpoint_blobs -------


def test_write_window_sql_casts_both_substring_bounds() -> None:
    """Same hazard as checkpoint_blobs: an uncast bigint offset/length makes

    ``substring(bytea, bigint, bigint)`` fail to resolve on PostgreSQL.
    """
    from backend.db.aurora_dataapi_saver import WRITE_WINDOW_SQL

    assert "FROM %s::integer FOR %s::integer" in WRITE_WINDOW_SQL


@pytest.mark.asyncio
async def test_large_pending_write_round_trips_byte_identically(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    """A pending write larger than one window must reassemble exactly.

    This includes the ``RESUME`` payload carrying a replayed interrupt
    answer, which is exactly the case that failed before windowing.
    """
    config = {"configurable": {"thread_id": "t1", "checkpoint_ns": "", "checkpoint_id": "cp1"}}
    value = ["y" * (MAX_ROW_BYTES * 2 + 7)]
    blob_type, payload = saver.serde.dumps_typed(value)
    segments = split_for_write(payload)
    assert len(segments) == 3, "test setup must produce three segments"

    client.results = [[{"returning": 1}]]
    await saver.aput_writes(config, [("messages", value)], "task-1")

    client.results = [
        [
            {
                "task_id": "task-1",
                "idx": 0,
                "channel": "messages",
                "type": blob_type,
                "n": len(payload),
            }
        ],
        [{"part": segments[0]}],
        [{"part": segments[1]}],
        [{"part": segments[2]}],
    ]
    pending = await saver._pending_writes("t1", "", "cp1")
    assert pending == [("task-1", "messages", value)]


@pytest.mark.asyncio
async def test_retried_ordinary_batch_does_not_double_append_segments(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    """A retried ordinary batch hits ``DO NOTHING`` (no ``RETURNING`` row) and

    must not append its segments again, or the stored blob doubles.
    """
    config = {"configurable": {"thread_id": "t1", "checkpoint_ns": "", "checkpoint_id": "cp1"}}
    value = "x" * (MAX_ROW_BYTES * 2 + 5)

    client.results = [[{"returning": 1}]]
    await saver.aput_writes(config, [("messages", value)], "task-1")
    first_appends = sum(1 for s in client.statements() if "blob || " in s)
    assert first_appends == 2, "three segments need two appends"

    client.results = [[]]
    await saver.aput_writes(config, [("messages", value)], "task-1")
    total_appends = sum(1 for s in client.statements() if "blob || " in s)
    assert total_appends == first_appends, (
        "a retry that hits DO NOTHING must not append again, or the stored "
        "blob doubles"
    )


# --- Important 2: alist's row body (inline/blob merge, pending_writes) -----


@pytest.mark.asyncio
async def test_alist_merges_inline_and_blob_channel_values(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    """Fails against a blob-only implementation: it would drop the inline

    primitives entirely instead of merging them with the blob-backed
    channel, and it would not let the blob win on the colliding key.
    """
    blob_type, payload = saver.serde.dumps_typed(["blob-msg"])
    checkpoint_row = {
        "v": 1,
        "id": "cp1",
        "ts": "2026-09-06T00:00:00+00:00",
        "channel_values": {
            "traveler_id": "TRV-001",
            "workflow_status": "interrupted",
            "messages": "stale-inline-value",
        },
        "channel_versions": {"messages": "1"},
        "versions_seen": {},
    }
    client.results = [
        [
            {
                "thread_id": "t1",
                "checkpoint_ns": "",
                "checkpoint_id": "cp1",
                "parent_checkpoint_id": None,
                "checkpoint": json.dumps(checkpoint_row),
                "metadata": json.dumps({"step": 1}),
            }
        ],
        [{"n": len(payload), "type": blob_type}],
        [{"part": payload}],
        [],
    ]

    [item] = [t async for t in saver.alist(_config())]
    values = item.checkpoint["channel_values"]
    assert values["traveler_id"] == "TRV-001"
    assert values["workflow_status"] == "interrupted"
    assert values["messages"] == ["blob-msg"], "the blob must win on collision"


@pytest.mark.asyncio
async def test_alist_yields_non_empty_pending_writes(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    """Fails against a blob-only implementation that never calls

    ``_pending_writes`` from within the row body, or drops the result.
    """
    checkpoint_row = _checkpoint("cp1")
    checkpoint_row["channel_versions"] = {}
    blob_type, payload = saver.serde.dumps_typed("resume-answer")
    client.results = [
        [
            {
                "thread_id": "t1",
                "checkpoint_ns": "",
                "checkpoint_id": "cp1",
                "parent_checkpoint_id": None,
                "checkpoint": json.dumps(checkpoint_row),
                "metadata": json.dumps({"step": 1}),
            }
        ],
        [
            {
                "task_id": "task-1",
                "idx": -1,
                "channel": "__resume__",
                "type": blob_type,
                "n": len(payload),
            }
        ],
        [{"part": payload}],
    ]

    [item] = [t async for t in saver.alist(_config())]
    assert item.pending_writes
    assert item.pending_writes[0][1] == "__resume__"


@pytest.mark.asyncio
async def test_aget_tuple_returns_non_empty_pending_writes(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    checkpoint_row = _checkpoint("cp1")
    checkpoint_row["channel_versions"] = {}
    blob_type, payload = saver.serde.dumps_typed("resume-answer")
    client.results = [
        [
            {
                "thread_id": "t1",
                "checkpoint_ns": "",
                "checkpoint_id": "cp1",
                "parent_checkpoint_id": None,
                "checkpoint": json.dumps(checkpoint_row),
                "metadata": json.dumps({"step": 1}),
            }
        ],
        [
            {
                "task_id": "task-1",
                "idx": -1,
                "channel": "__resume__",
                "type": blob_type,
                "n": len(payload),
            }
        ],
        [{"part": payload}],
    ]

    tuple_ = await saver.aget_tuple(_config())
    assert tuple_ is not None
    assert tuple_.pending_writes


# --- Important 3: alist rejects filter instead of ignoring it --------------


@pytest.mark.asyncio
async def test_alist_raises_for_nonempty_filter(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    with pytest.raises(NotImplementedError):
        [item async for item in saver.alist(_config(), filter={"step": 3})]


@pytest.mark.asyncio
async def test_alist_accepts_none_and_empty_filter(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    client.results = [[]]
    [item async for item in saver.alist(_config(), filter=None)]
    client.results = [[]]
    [item async for item in saver.alist(_config(), filter={})]


# --- Important 4: alist carries parent_config -------------------------------


@pytest.mark.asyncio
async def test_alist_carries_parent_config_when_present(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    checkpoint_row = _checkpoint("cp2")
    checkpoint_row["channel_versions"] = {}
    client.results = [
        [
            {
                "thread_id": "t1",
                "checkpoint_ns": "",
                "checkpoint_id": "cp2",
                "parent_checkpoint_id": "cp1",
                "checkpoint": json.dumps(checkpoint_row),
                "metadata": json.dumps({"step": 2}),
            }
        ],
        [],
    ]
    [item] = [t async for t in saver.alist(_config())]
    assert item.parent_config == {
        "configurable": {"thread_id": "t1", "checkpoint_ns": "", "checkpoint_id": "cp1"}
    }


@pytest.mark.asyncio
async def test_alist_parent_config_is_none_without_a_parent(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    checkpoint_row = _checkpoint("cp1")
    checkpoint_row["channel_versions"] = {}
    client.results = [
        [
            {
                "thread_id": "t1",
                "checkpoint_ns": "",
                "checkpoint_id": "cp1",
                "parent_checkpoint_id": None,
                "checkpoint": json.dumps(checkpoint_row),
                "metadata": json.dumps({"step": 1}),
            }
        ],
        [],
    ]
    [item] = [t async for t in saver.alist(_config())]
    assert item.parent_config is None


# --- Minor: limit clamping and required thread_id ---------------------------


@pytest.mark.asyncio
async def test_alist_clamps_zero_limit_to_one(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    client.results = [[]]
    [item async for item in saver.alist(_config(), limit=0)]
    _, params = client.calls[0]
    assert params[-1] == 1


@pytest.mark.asyncio
async def test_alist_clamps_negative_limit_to_one(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    client.results = [[]]
    [item async for item in saver.alist(_config(), limit=-5)]
    _, params = client.calls[0]
    assert params[-1] == 1


@pytest.mark.asyncio
async def test_alist_raises_without_a_config(saver: AuroraDataApiSaver) -> None:
    with pytest.raises(ValueError, match="thread_id"):
        [item async for item in saver.alist(None)]


@pytest.mark.asyncio
async def test_alist_raises_when_config_lacks_thread_id(
    saver: AuroraDataApiSaver,
) -> None:
    with pytest.raises(ValueError, match="thread_id"):
        [item async for item in saver.alist({"configurable": {}})]
