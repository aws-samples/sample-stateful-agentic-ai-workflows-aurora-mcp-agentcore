"""The Phase 5 memory branch embeds the query before it opens the traveler's transaction."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

from backend.agents.phase_05_workflow import memory_recall
from backend.agents.phase_05_workflow.memory_recall import workflow_memory_recall


class OrderedStore:
    def __init__(self):
        self.events = []
        self.db = SimpleNamespace(scoped_session=self._scoped_session)

    @asynccontextmanager
    async def _scoped_session(self, **_kwargs):
        self.events.append("transaction opened")
        yield "tx_1"

    def prepare_embedding_vector(self, text, *, input_type):
        self.events.append(f"embedded {text} as {input_type}")
        return "[0.1]"

    async def recall_preferences(self, *_args, **_kwargs):
        return []

    async def recall_short_term(self, *_args, **_kwargs):
        return []

    async def recall_similar_interactions(self, *_args, **kwargs):
        self.events.append(("similar", kwargs["query_vector"], kwargs["embedding_prepared"]))
        return []


async def test_the_query_is_embedded_before_the_transaction_and_passed_prepared(monkeypatch):
    store = OrderedStore()
    monkeypatch.setattr("backend.memory.store.get_memory_store", lambda: store)
    monkeypatch.setattr(
        memory_recall,
        "get_agentcore_identity",
        lambda: SimpleNamespace(authorization_context=lambda: {}),
    )

    async def search(_query, limit=5):
        return [], []

    monkeypatch.setattr(memory_recall, "retrieval_search", search)

    await workflow_memory_recall("beach", traveler_id="trv_x", conversation_id="t1")

    assert store.events == [
        "embedded beach as search_query",
        "transaction opened",
        ("similar", "[0.1]", True),
    ]
