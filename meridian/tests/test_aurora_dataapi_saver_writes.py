"""Pending writes and history for the Data API saver.

Pending writes are what a resumed graph replays. Losing one, renumbering a
reserved index, or dropping task_path turns a resume into a silent
recomputation, so each is pinned here.
"""

import pytest

from backend.db.aurora_dataapi_saver import AuroraDataApiSaver
from tests.test_aurora_dataapi_saver import FakeDataClient, _config


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
