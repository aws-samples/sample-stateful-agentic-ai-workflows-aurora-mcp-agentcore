"""A cancelled segmented write releases its transaction before another worker resumes."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from backend.db.aurora_dataapi_saver import AuroraDataApiSaver, INSERT_WRITE_SQL


async def test_cancelled_pending_write_rolls_back_without_committing():
    client = Mock()
    client.begin_transaction.return_value = "checkpoint-tx"
    client.execute = AsyncMock(side_effect=[[{"written": 1}], asyncio.CancelledError()])
    saver = AuroraDataApiSaver(client)
    with pytest.raises(asyncio.CancelledError):
        await saver._write_pending_blob(
            INSERT_WRITE_SQL, ("thread", "", "cp", "task", 0), (), [b"first", b"second"]
        )
    client.rollback_transaction.assert_called_once_with("checkpoint-tx")
    client.commit_transaction.assert_not_called()
