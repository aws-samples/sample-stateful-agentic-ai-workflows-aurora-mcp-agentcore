"""A batch of pending writes lands in one Data API transaction or not at all."""

from unittest.mock import AsyncMock, Mock

import pytest
from langgraph.checkpoint.base import WRITES_IDX_MAP

from examples.langgraph.aurora_dataapi_saver import (
    INSERT_WRITE_SQL,
    UPSERT_WRITE_SQL,
    AuroraDataApiSaver,
)

CONFIG = {"configurable": {"thread_id": "t", "checkpoint_ns": "", "checkpoint_id": "cp"}}
RESERVED = next(iter(WRITES_IDX_MAP))


def _saver(execute=None):
    client = Mock()
    client.begin_transaction.return_value = "tx-1"
    client.execute = execute or AsyncMock(return_value=[{"written": 1}])
    return AuroraDataApiSaver(client), client


@pytest.mark.asyncio
async def test_a_batch_commits_once_and_every_write_uses_the_transaction():
    saver, client = _saver()
    await saver.aput_writes(CONFIG, [("a", 1), ("b", 2), ("c", 3)], "task")
    client.begin_transaction.assert_called_once()
    client.commit_transaction.assert_called_once_with("tx-1")
    client.rollback_transaction.assert_not_called()
    assert client.execute.await_count == 3
    assert {c.kwargs["transaction_id"] for c in client.execute.await_args_list} == {"tx-1"}


@pytest.mark.asyncio
async def test_a_failing_write_rolls_back_the_whole_batch():
    execute = AsyncMock(side_effect=[[{"written": 1}], RuntimeError("data api down")])
    saver, client = _saver(execute)
    with pytest.raises(RuntimeError, match="data api down"):
        await saver.aput_writes(CONFIG, [("a", 1), ("b", 2), ("c", 3)], "task")
    client.begin_transaction.assert_called_once()
    client.rollback_transaction.assert_called_once_with("tx-1")
    client.commit_transaction.assert_not_called()


@pytest.mark.asyncio
async def test_reserved_batches_upsert_and_ordinary_batches_do_not_overwrite():
    saver, client = _saver()
    await saver.aput_writes(CONFIG, [(RESERVED, 1)], "task")
    assert client.execute.await_args_list[0].args[0] == UPSERT_WRITE_SQL
    await saver.aput_writes(CONFIG, [(RESERVED, 1), ("a", 2)], "task")
    assert client.execute.await_args_list[1].args[0] == INSERT_WRITE_SQL
