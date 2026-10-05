"""A cancelled segmented write releases its transaction before another worker resumes."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

import backend.db.aurora_dataapi_saver as saver_module
from backend.db.aurora_dataapi_saver import AuroraDataApiSaver


async def test_cancelled_pending_write_rolls_back_without_committing(monkeypatch):
    monkeypatch.setattr(saver_module, "split_for_write", lambda payload: [b"first", b"second"])
    client = Mock()
    client.begin_transaction.return_value = "checkpoint-tx"
    client.execute = AsyncMock(side_effect=[[{"written": 1}], asyncio.CancelledError()])
    saver = AuroraDataApiSaver(client)
    config = {"configurable": {"thread_id": "thread", "checkpoint_ns": "", "checkpoint_id": "cp"}}
    with pytest.raises(asyncio.CancelledError):
        await saver.aput_writes(config, [("channel", "value")], "task")
    client.rollback_transaction.assert_called_once_with("checkpoint-tx")
    client.commit_transaction.assert_not_called()
