"""Production turns must not hold RLS transactions across the runtime call."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

from backend.agentcore.runtime import RuntimeDecision
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

    async def execute(self, _sql, params=(), **_kwargs):
        assert self.active_tx is None
        self.events.append("aurora:hydrate")
        return [
            {"package_id": package_id, "name": "Tokyo Replan", "price_per_person": 2500.0,
             "durations": ["7 nights"], "availability": {"7 nights": 4}}
            for package_id in params
        ]


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

    async def recall_preferences(self, _traveler_id, limit=8, transaction_id=None):
        assert transaction_id == self.db.active_tx
        assert limit >= 20, "the budget ceiling must see every fact, not the top eight"
        self.events.append("aurora:budget-facts")
        return [
            {"key": "home_airport", "value": "JFK", "source": "profile", "confidence": 1.0},
            {"key": "budget_cap", "value": "$3,200", "source": "search_analytics", "confidence": 0.88},
        ]

    @staticmethod
    def format_memory_context(*_args):
        return "Alex flies from JFK"

    async def write_audit(self, *, transaction_id, **kwargs):
        assert transaction_id == self.db.active_tx
        self.events.append(f"aurora:audit:{kwargs.get('operation')}")


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
                {"key": "home_airport", "value": "JFK", "source": "profile", "confidence": 1.0},
                {"key": "budget", "value": "Prefers $2k-3.5k per person", "source": "profile",
                 "confidence": 0.9},
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


def build_agent(events, decision, calls):
    db = FakeDB(events)
    agent = ProductionAgent.__new__(ProductionAgent)
    agent.activity_callback = lambda _entry: None
    agent.store = FakeStore(db, events)
    agent.db = db
    agent.traveler_memory = FakeMemoryAgent(db, events)
    agent.identity = SimpleNamespace(
        scope_for_turn=lambda: SimpleNamespace(
            iam_identity="arn:aws:sts::123:assumed-role/demo/session",
            workload_identity="workload/demo",
            resource_provider="meridian",
            token_status="live",
            authorization=SimpleNamespace(provider="agentcore_workload", subject_id="workload/demo"),
        )
    )

    def invoke_turn(*args, **kwargs):
        assert db.active_tx is None
        events.append("external:runtime")
        calls.append((args, kwargs))
        return decision

    agent.agentcore_runtime = SimpleNamespace(invoke_turn=invoke_turn)
    return agent


def runtime_decision(**overrides):
    base = dict(
        runtime_arn="arn:aws:bedrock-agentcore:us-east-1:1:runtime/rt-1",
        runtime_session_id="runtime-session-id",
        qualifier="DEFAULT",
        message="Tokyo options grounded in traveler context.",
        recommended_package_ids=["pkg-1"],
        follow_ups=[],
        activities=[{"id": "rt-1", "timestamp": "t", "activity_type": "search",
                     "title": "AgentCore Gateway · tools/call → semantic_trip_search"}],
        packages=[{"package_id": "pkg-1", "name": "Tokyo Replan", "similarity": 0.9}],
        trace_id="abc123",
        usage={"inputTokens": 10, "outputTokens": 5},
        elapsed_ms=1200,
    )
    base.update(overrides)
    return RuntimeDecision(**base)


def test_production_turn_releases_transactions_before_the_runtime_call(monkeypatch):
    events, calls = [], []
    agent = build_agent(events, runtime_decision(), calls)
    monkeypatch.setattr(concierge_mod, "require_agentcore_platform", lambda **_kw: None)

    result = asyncio.run(
        agent.process_turn("Rework my Tokyo trip", "trv_meridian_demo", None, 5, travelers_count=2)
    )

    packages, activities, message, conv_id, facts = result
    assert conv_id == "conv-test"
    assert message == "Tokyo options grounded in traveler context."
    assert packages[0].package_id == "pkg-1"
    assert packages[0].similarity == 0.9
    assert packages[0].availability == {"7 nights": 4}
    assert facts[1]["key"] == "budget"
    assert agent.db.tx_count == 2
    read_commit = events.index("tx-1:commit")
    write_open = events.index("tx-2:open")
    runtime_call = events.index("external:runtime")
    assert read_commit < runtime_call < write_open
    assert events.index("aurora:hydrate") < write_open
    assert "aurora:audit:production_turn" in events
    _args, kwargs = calls[0]
    assert kwargs["budget_ceiling_cents"] == 640000
    assert kwargs["travelers_count"] == 2
    assert events.index("aurora:budget-facts") < events.index("tx-1:commit")
    assert "hold_confirmed" not in kwargs
    titles = [entry.title for entry in activities]
    assert "AgentCore Gateway · tools/call → semantic_trip_search" in titles
    assert "AgentCore Runtime · turn complete" in titles
    runtime_span = next(a for a in activities if a.title == "AgentCore Runtime · turn complete")
    labels = {f["label"]: f["value"] for f in runtime_span.telemetry["fields"]}
    assert labels["trace_id"] == "abc123"
    assert labels["trace_console"].endswith("rt-1-DEFAULT")
