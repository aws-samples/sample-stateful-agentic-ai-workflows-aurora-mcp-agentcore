"""Hosted rehearsal cleanup owns both workflow and concierge journey threads."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from scripts import validate_demo


@pytest.mark.asyncio
async def test_cleanup_removes_only_journeys_for_its_own_conversation(monkeypatch):
    from backend.db import rds_data_client
    from scripts import kill_and_resume_demo

    rows = [
        {"journey_id": "owned-workflow", "thread_id": "owned-conversation"},
        {"journey_id": "owned-concierge", "thread_id": "concierge:owned-conversation"},
    ]
    db = SimpleNamespace(execute=AsyncMock(side_effect=[rows, [], [], []]))
    purge = AsyncMock()
    monkeypatch.setattr(validate_demo, "steps", [{"conversation_id": "owned-conversation"}])
    monkeypatch.setattr(rds_data_client, "get_rds_data_client", lambda: db)
    monkeypatch.setattr(kill_and_resume_demo, "_purge", purge)

    await validate_demo.cleanup()

    query, parameters = db.execute.await_args_list[0].args
    assert "WHERE thread_id IN (%s, %s)" in query
    assert parameters == ("owned-conversation", "concierge:owned-conversation")
    assert [call.args for call in purge.await_args_list] == [
        (db, "owned-workflow", "owned-conversation"),
        (db, "owned-concierge", "concierge:owned-conversation"),
    ]
    for call in db.execute.await_args_list[1:]:
        assert "WHERE conversation_id = %s" in call.args[0]
        assert call.args[1] == ("owned-conversation",)
