"""The memory branch reads preferences, session turns and similar trips in one traveler scope."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from backend.agents.phase_05_workflow import memory_recall
from backend.agents.phase_05_workflow.memory_recall import workflow_memory_recall
from backend.memory.store import DEMO_TRAVELER_ID

AUTHORIZATION = {"subject": "test"}


class StoreFake:
    """The memory store's boundary: records every scope opened and every read made."""

    def __init__(self):
        self.scopes = []
        self.reads = []
        self.db = SimpleNamespace(scoped_session=self._scoped_session)

    @asynccontextmanager
    async def _scoped_session(self, **kwargs):
        self.scopes.append(kwargs)
        yield "tx_1"

    async def recall_preferences(self, traveler_id, *, transaction_id):
        self.reads.append(("preferences", traveler_id, transaction_id))
        return ["nonstop", "window"]

    async def recall_short_term(self, conversation_id, *, limit, transaction_id):
        self.reads.append(("short_term", conversation_id, limit, transaction_id))
        return ["turn"]

    async def recall_similar_interactions(self, traveler_id, query, *, limit, transaction_id):
        self.reads.append(("similar", traveler_id, query, limit, transaction_id))
        return []


@pytest.fixture
def store(monkeypatch):
    fake = StoreFake()
    monkeypatch.setattr("backend.memory.store.get_memory_store", lambda: fake)
    monkeypatch.setattr(
        memory_recall,
        "get_agentcore_identity",
        lambda: SimpleNamespace(authorization_context=lambda: AUTHORIZATION),
    )

    async def search(query, limit=5):
        return ["pkg"], [f"search span for {query} x{limit}"]

    monkeypatch.setattr(memory_recall, "retrieval_search", search)
    return fake


async def test_recall_reads_all_three_sources_in_one_scope_then_searches(store):
    products, activities = await workflow_memory_recall(
        "Recall my Tokyo plan", traveler_id="trv_x", conversation_id="t1"
    )

    assert store.scopes == [
        {"traveler_id": "trv_x", "agent_type": "workflow_agent", "authorization": AUTHORIZATION}
    ]
    assert store.reads == [
        ("preferences", "trv_x", "tx_1"),
        ("short_term", "t1", 6, "tx_1"),
        ("similar", "trv_x", "Recall my Tokyo plan", 3, "tx_1"),
    ]
    assert products == ["pkg"]
    assert [(a.title, a.details) for a in activities[:3]] == [
        ("Aurora recall: traveler_preferences", "2 durable preference facts"),
        ("Aurora recall: conversation_messages", "1 recent session turns"),
        ("Aurora recall: trip_interactions (pgvector)", "0 semantically similar past interactions"),
    ]
    assert activities[3] == "search span for Recall my Tokyo plan x5"


async def test_recall_without_a_conversation_skips_the_session_read(store):
    _products, activities = await workflow_memory_recall(
        "Recall my plan", traveler_id="trv_x", conversation_id=""
    )
    assert [read[0] for read in store.reads] == ["preferences", "similar"]
    assert "Aurora recall: conversation_messages" not in [
        getattr(a, "title", None) for a in activities
    ]


async def test_recall_without_a_traveler_uses_the_demo_traveler(store):
    await workflow_memory_recall("Recall my plan", traveler_id="", conversation_id="t1")
    assert store.scopes[0]["traveler_id"] == DEMO_TRAVELER_ID
    assert store.reads[0][1] == DEMO_TRAVELER_ID
