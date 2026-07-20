"""Production turns must not hold RLS transactions across external calls."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

from backend.agents.production_04 import concierge as concierge_mod
from backend.agents.production_04.concierge import ProductionAgent


class FakeDB:
    def __init__(self, events):
        self.events = events
        self.active_tx = None
        self.tx_count = 0

    @asynccontextmanager
    async def scoped_session(self, **_kwargs):
        self.tx_count += 1
        tx = f"tx-{self.tx_count}"
        assert self.active_tx is None
        self.active_tx = tx
        self.events.append(f"{tx}:open")
        try:
            yield tx
            self.events.append(f"{tx}:commit")
        finally:
            self.active_tx = None


class FakeStore:
    def __init__(self, db, events):
        self.db = db
        self.events = events

    def prepare_embedding_vector(self, _text, *, input_type):
        assert self.db.active_tx is None
        self.events.append(f"embed:{input_type}")
        return "[0.1,0.2]"

    async def get_or_create_conversation(
        self, _traveler_id, _conversation_id, *, transaction_id
    ):
        assert transaction_id == self.db.active_tx
        self.events.append("aurora:conversation")
        return "conv-test"

    async def recall_profile(self, _traveler_id, *, transaction_id):
        assert transaction_id == self.db.active_tx
        self.events.append("aurora:profile")
        return {"home_airport": "JFK"}

    @staticmethod
    def format_memory_context(*_args):
        return "Alex flies from JFK"

    async def write_audit(self, *, transaction_id, **_kwargs):
        assert transaction_id == self.db.active_tx
        self.events.append("aurora:audit")


class FakeMemoryAgent:
    def __init__(self, db, events):
        self.db = db
        self.events = events
        self.activity_callback = lambda _entry: None
        self._transaction_id = None
        self._prepared_query_vector = None
        self._query_vector_prepared = False
        self._prepared_turn_vectors = None

    def _assert_scoped(self):
        assert self._transaction_id == self.db.active_tx
        assert self._transaction_id is not None

    async def recall_session_context(self, _conversation_id):
        self._assert_scoped()
        self.events.append("aurora:session")
        return {"turns": []}

    async def recall_traveler_preferences(self, _traveler_id):
        self._assert_scoped()
        self.events.append("aurora:preferences")
        return {
            "facts": [
                {
                    "key": "home_airport",
                    "value": "JFK",
                    "source": "profile",
                    "confidence": 1.0,
                }
            ]
        }

    async def recall_similar_interactions(self, _traveler_id, _message):
        self._assert_scoped()
        assert self._query_vector_prepared
        self.events.append("aurora:semantic-recall")
        return {"interactions": []}

    async def persist_turn(self, *_args):
        self._assert_scoped()
        assert self._prepared_turn_vectors
        self.events.append("aurora:persist")


def test_production_turn_releases_transactions_before_external_calls(monkeypatch):
    events = []
    db = FakeDB(events)
    store = FakeStore(db, events)
    memory_agent = FakeMemoryAgent(db, events)

    agent = ProductionAgent.__new__(ProductionAgent)
    agent.activity_callback = lambda _entry: None
    agent.store = store
    agent.db = db
    agent.traveler_memory = memory_agent
    agent.identity = SimpleNamespace(
        scope_for_turn=lambda: SimpleNamespace(
            iam_identity="arn:aws:sts::123:assumed-role/demo/session",
            workload_identity="workload/demo",
            resource_provider="meridian",
            token_status="live",
            authorization=SimpleNamespace(
                provider="agentcore_workload",
                subject_id="workload/demo",
            ),
        )
    )

    def external(name, result):
        def call(*_args, **_kwargs):
            assert db.active_tx is None
            events.append(name)
            return result

        return call

    agent.agentcore_runtime = SimpleNamespace(
        session_for_turn=external(
            "external:runtime",
            SimpleNamespace(
                runtime_session_id="runtime-session-id",
                invoke_status="ready",
                runtime_arn="arn:runtime",
                qualifier="DEFAULT",
                isolation="microVM",
            ),
        )
    )
    agent.agentcore_memory = SimpleNamespace(
        memory_id="memory-id",
        _namespace=lambda traveler_id, conversation_id: (
            f"/users/{traveler_id}/sessions/{conversation_id}"
        ),
        list_recent_turns=external("external:memory-list", []),
        semantic_recall=external("external:memory-recall", []),
        record_turn=external(
            "external:memory-write",
            {"status": "ok", "event_id": "evt-1"},
        ),
    )

    async def fake_search(_message, _limit):
        assert db.active_tx is None
        events.append("external:gateway")
        return (
            [
                SimpleNamespace(
                    package_id="pkg-1",
                    name="Tokyo Replan",
                )
            ],
            [],
        )

    agent._search_packages = fake_search
    monkeypatch.setattr(concierge_mod, "require_agentcore_platform", lambda: None)

    result = asyncio.run(
        agent.process_turn(
            "Rework my Tokyo trip",
            "trv_meridian_demo",
            None,
            5,
        )
    )

    assert result[3] == "conv-test"
    assert db.tx_count == 2
    read_commit = events.index("tx-1:commit")
    write_commit = events.index("tx-2:commit")
    for external_event in (
        "external:runtime",
        "external:memory-list",
        "external:memory-recall",
        "external:gateway",
    ):
        assert events.index(external_event) > read_commit
        assert events.index(external_event) < events.index("tx-2:open")
    assert events.index("external:memory-write") > write_commit
    assert memory_agent._transaction_id is None
    assert memory_agent._prepared_turn_vectors is None
