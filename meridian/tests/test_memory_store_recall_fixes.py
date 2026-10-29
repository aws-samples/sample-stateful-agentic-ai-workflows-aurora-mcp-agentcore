"""Recall ordering, stored confidence and event-loop safety in ``MemoryStore``."""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.agentcore.identity import get_agentcore_identity
from backend.db.rds_data_client import get_rds_data_client
from backend.memory.store import MemoryStore


class RecordingDb:
    """Records every statement and answers with queued rows."""

    def __init__(self, rows=None):
        self.calls = []
        self.rows = rows or []

    async def execute(self, sql, params=(), *, transaction_id=None):
        self.calls.append((sql, params, transaction_id))
        return self.rows


def make_store(rows=None):
    store = MemoryStore.__new__(MemoryStore)
    store.db = RecordingDb(rows)
    store.embeddings = MagicMock()
    store.embeddings.generate_text_embedding.return_value = [0.25, 0.5]
    return store, store.db


@pytest.mark.parametrize(
    ("with_embedding", "content", "kwargs"),
    [
        (True, "hello", {}),
        (True, "hello", {"embedding_prepared": True, "embedding_vector": None}),
        (False, "hello", {}),
    ],
    ids=["with vector", "vector unavailable", "without embedding"],
)
async def test_each_message_insert_sets_created_at_to_statement_time(
    with_embedding, content, kwargs
):
    store, db = make_store()

    await store.append_message(
        "conv_1", "user", content, with_embedding=with_embedding, **kwargs
    )

    insert = next(sql for sql, _p, _t in db.calls if "INSERT INTO conversation_messages" in sql)
    assert "created_at" in insert
    assert "clock_timestamp()" in insert


async def test_the_vector_insert_sets_created_at_to_statement_time():
    store, db = make_store()

    await store.append_message("conv_1", "user", "hello")

    insert = db.calls[0][0]
    assert "::vector" in insert
    assert "clock_timestamp()" in insert


async def test_short_term_recall_breaks_timestamp_ties_by_turn_order_not_message_id():
    store, db = make_store()

    await store.recall_short_term("conv_1", limit=6)

    sql = db.calls[0][0]
    assert "ORDER BY created_at DESC," in sql
    assert "message_id" not in sql
    assert sql.index("'assistant' THEN 2") < sql.index("'user' THEN 1")


@pytest.mark.parametrize(
    ("stored", "reported"),
    [(0.0, 0.0), (0, 0.0), (0.9, 0.9), (None, 0.5), ("0.30", 0.3)],
)
async def test_recall_preferences_reports_the_stored_confidence(stored, reported):
    row = {
        "preference_key": "seat",
        "preference_value": "aisle",
        "preference_type": "explicit",
        "source": None,
        "confidence": stored,
    }
    store, _db = make_store([row])

    facts = await store.recall_preferences("trv_1")

    assert facts[0]["confidence"] == reported


async def test_recall_preferences_sorts_a_null_confidence_last():
    store, db = make_store([])

    await store.recall_preferences("trv_1")

    assert "ORDER BY confidence DESC NULLS LAST, last_seen_at DESC" in db.calls[0][0]


def block_for_a_second(*_args, **_kwargs):
    time.sleep(1.0)
    return [0.25, 0.5]


async def ticks_while(coro) -> int:
    ticks = 0
    stop = asyncio.Event()

    async def ticker():
        nonlocal ticks
        while not stop.is_set():
            ticks += 1
            await asyncio.sleep(0.05)

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0)
    await coro
    stop.set()
    await task
    return ticks


async def test_unprepared_recall_embeds_off_the_event_loop():
    store, _db = make_store()
    store.embeddings.generate_text_embedding.side_effect = block_for_a_second

    ticks = await ticks_while(store.recall_similar_interactions("trv_1", "beach"))

    assert ticks >= 10


async def test_unprepared_append_message_embeds_off_the_event_loop():
    store, _db = make_store()
    store.embeddings.generate_text_embedding.side_effect = block_for_a_second

    ticks = await ticks_while(store.append_message("conv_1", "user", "hello"))

    assert ticks >= 10


async def test_unprepared_persist_interaction_embeds_off_the_event_loop():
    store, _db = make_store()
    store.embeddings.generate_text_embedding.side_effect = block_for_a_second

    ticks = await ticks_while(store.persist_interaction("trv_1", "conv_1", "q", "a"))

    assert ticks >= 10


class MemoryServerStore:
    """Records whether the embedding was made before the transaction opened."""

    def __init__(self):
        self.events = []
        self.db = SimpleNamespace(scoped_session=self._scoped_session)

    @asynccontextmanager
    async def _scoped_session(self, **_kwargs):
        self.events.append("transaction opened")
        yield "tx_1"

    def prepare_embedding_vector(self, _text, *, input_type):
        self.events.append(f"embedded {input_type}")
        return "[0.1,0.2]"

    async def recall_similar_interactions(self, *_args, **kwargs):
        self.events.append(("recall", kwargs["query_vector"], kwargs["embedding_prepared"]))
        return []

    async def get_or_create_conversation(self, **_kwargs):
        return "conv_1"

    async def append_message(self, **kwargs):
        self.events.append(
            ("append", kwargs["embedding_vector"], kwargs["embedding_prepared"])
        )
        return "msg_1"


@pytest.fixture
def memory_server(monkeypatch):
    from backend.mcp import memory_server as server

    fake = MemoryServerStore()
    monkeypatch.setattr(server, "_store", lambda: fake)
    monkeypatch.setattr(server, "_authorization", lambda: {"subject": "test"})
    return server, fake


async def test_the_memory_server_embeds_a_recall_query_before_the_transaction(memory_server):
    server, fake = memory_server

    await server.semantic_recall_interactions("trv_1", "beach")

    assert fake.events == [
        "embedded search_query",
        "transaction opened",
        ("recall", "[0.1,0.2]", True),
    ]


async def test_the_memory_server_embeds_a_persisted_turn_before_the_transaction(memory_server):
    server, fake = memory_server

    await server.persist_turn("conv_1", "trv_1", "user", "hello")

    assert fake.events == [
        "embedded search_document",
        "transaction opened",
        ("append", "[0.1,0.2]", True),
    ]


@pytest.mark.database
async def test_a_persisted_turn_reads_back_assistant_before_user():
    """Both rows of a turn share a transaction; the newest-first read must still order them."""
    store = MemoryStore()
    client = get_rds_data_client()
    traveler = "trv_meridian_demo"
    conversation = f"conv_itest_{uuid.uuid4().hex[:8]}"
    try:
        async with client.scoped_session(
            traveler_id=traveler,
            agent_type="booking_agent",
            authorization=get_agentcore_identity().authorization_context(),
        ) as tx:
            await store.get_or_create_conversation(traveler, conversation, transaction_id=tx)
            await store.append_message(
                conversation, "user", "Find me a beach trip", with_embedding=False,
                transaction_id=tx,
            )
            await store.append_message(
                conversation, "assistant", "Here are three beaches", with_embedding=False,
                transaction_id=tx,
            )
        async with client.scoped_session(
            traveler_id=traveler,
            agent_type="booking_agent",
            authorization=get_agentcore_identity().authorization_context(),
        ) as tx:
            rows = await store.recall_short_term(conversation, limit=6, transaction_id=tx)
        assert [row["role"] for row in rows] == ["assistant", "user"]
    finally:
        await client.execute(
            "DELETE FROM conversation_messages WHERE conversation_id = %s", (conversation,)
        )
        await client.execute(
            "DELETE FROM conversations WHERE conversation_id = %s", (conversation,)
        )
