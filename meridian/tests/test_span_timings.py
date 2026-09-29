"""Every span duration is measured around the real call it labels.

The clock here only moves inside the fake calls, by a fixed amount each. A
span that shows exactly that amount was timed around that call; any other
number (a sum, a subtraction, a guess) fails.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import backend.llm_polish as llm_polish
import backend.routers.chat as chat_router
from backend import timing
from backend.agentcore.runtime import RuntimeDecision, _apply_result
from backend.agents.orchestration_05.workflow import _with_checkpoint_timings
from backend.agents.production_04.concierge import ProductionAgent
from backend.agents.retrieval_03.search_agent import SearchAgent
from backend.db.aurora_dataapi_saver import AuroraDataApiSaver
from backend.llm_polish import PolishResult


class FakeClock:
    """A perf_counter that only advances when a fake call says so."""

    def __init__(self) -> None:
        self.now = 1000.0

    def read(self) -> float:
        return self.now

    def spend(self, ms: int) -> None:
        self.now += ms / 1000


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    fake = FakeClock()
    monkeypatch.setattr(timing, "_clock", fake.read)
    return fake


def _span(activities, title):
    matches = [a for a in activities if _title(a) == title]
    assert matches, f"no span titled {title!r} in {[_title(a) for a in activities]}"
    return matches[0]


def _title(activity):
    return activity["title"] if isinstance(activity, dict) else activity.title


def _ms(activity):
    return activity["execution_time_ms"] if isinstance(activity, dict) else activity.execution_time_ms


# --------------------------------------------------------------- Bedrock polish

def test_polish_times_only_the_call_of_the_model_that_wrote_the_reply(clock, monkeypatch):
    calls = []

    def converse(**kwargs):
        calls.append(kwargs["modelId"])
        if len(calls) == 1:
            clock.spend(5000)
            raise RuntimeError("throttled")
        clock.spend(1200)
        return {
            "stopReason": "end_turn",
            "output": {"message": {"content": [{"text": "Here is your trip."}]}},
        }

    monkeypatch.setattr(llm_polish, "_client", lambda: SimpleNamespace(converse=converse))
    result = llm_polish._polish_sync("Find a retreat", "facts")

    assert result.model_id == calls[1]
    assert result.elapsed_ms == 1200


def test_polish_span_carries_the_measured_call_time(monkeypatch):
    async def polished(_query, _facts):
        return PolishResult(
            text="Polished.", model_id="global.anthropic.claude-sonnet-5", note=None,
            elapsed_ms=1400,
        )

    monkeypatch.setattr(chat_router, "polish_concierge_reply", polished)
    activities: list = []
    asyncio.run(chat_router._polish_and_record(
        phase=3, mode_label="Retrieval", agent_name="RetrievalAgent",
        user_query="Find a retreat", raw_message="Two trips.", products=[],
        activities=activities,
    ))
    assert _ms(activities[-1]) == 1400


# ------------------------------------------------------------------------ MCP

class FakeConcierge:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock

    async def call(self, name, args):
        self.clock.spend(180)
        return {"destination": args.get("destination"), "low": 1599.0, "average": 2607.0,
                "high": 3899.0, "sample_size": 6, "note": "Not seasonal."}


class FakePostgresMcp:
    available_tools = [{"name": "run_query"}]

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock

    async def run_query(self, _sql):
        self.clock.spend(75)
        return []


def _fake_sessions(monkeypatch, clock):
    @asynccontextmanager
    async def concierge():
        yield FakeConcierge(clock)

    @asynccontextmanager
    async def postgres():
        yield FakePostgresMcp(clock)

    monkeypatch.setattr(chat_router, "concierge_mcp_session", concierge)
    monkeypatch.setattr(chat_router, "mcp_session", postgres)


def test_mcp_tool_span_carries_the_tool_call_time(clock, monkeypatch):
    _fake_sessions(monkeypatch, clock)
    _products, activities, _text = asyncio.run(chat_router.mcp_search(
        "What is the price range for Tokyo trips?", traveler_id="trv_meridian_demo",
    ))
    assert _ms(_span(activities, "meridian-concierge · price_range")) == 180


def test_postgres_mcp_span_carries_the_query_time(clock, monkeypatch):
    _fake_sessions(monkeypatch, clock)
    _products, activities, _text = asyncio.run(chat_router.mcp_search(
        "Show me city trips under $2,000 per traveler.", traveler_id="trv_meridian_demo",
    ))
    assert _ms(_span(activities, "postgres-mcp · run_query")) == 75


# ------------------------------------------------------------ Aurora and Bedrock

ROW = {
    "package_id": "AML-002", "name": "Amalfi Coast Villa Week", "operator": "Costa",
    "price_per_person": 1599.0, "description": "Villa", "image_url": "", "trip_type": "Beach",
    "destination": "Positano", "region": "Europe", "durations": ["5 nights"],
    "availability": {"5 nights": 3}, "highlights": [], "similarity": 0.4,
}


class FakeDb:
    """Each statement costs the time its first matching marker names."""

    def __init__(self, clock: FakeClock, costs: dict[str, int]) -> None:
        self.clock = clock
        self.costs = costs

    async def execute(self, sql, _params=None):
        for marker, ms in self.costs.items():
            if marker in sql:
                self.clock.spend(ms)
                break
        return [dict(ROW)]


class FakeEmbeddings:
    last_model_used = "cohere.embed-v4:0"

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock

    def generate_text_embedding(self, _text, **_kwargs):
        self.clock.spend(200)
        return [0.1, 0.2, 0.3]

    def rerank_documents(self, _query, _docs, top_n=5):
        self.clock.spend(90)
        return [{"index": 0, "score": 0.9}]


def test_workflow_search_spans_time_each_call_directly(clock, monkeypatch):
    monkeypatch.setattr(chat_router, "get_rds_data_client", lambda: FakeDb(
        clock, {"semantic_trip_search": 120, "ts_rank": 30},
    ))
    monkeypatch.setattr(chat_router, "get_embedding_service", lambda: FakeEmbeddings(clock))
    _products, activities = asyncio.run(chat_router.retrieval_search("quiet villa"))

    assert _ms(_span(activities, "Embedding generated")) == 200
    # The two retrieval arms, measured together, and nothing else.
    assert _ms(_span(activities, "Hybrid candidates fetched")) == 150
    assert _ms(_span(activities, "Cohere rerank applied")) == 90


def test_package_agent_times_its_query_and_invents_nothing_for_in_memory_work(clock, monkeypatch):
    monkeypatch.setattr(chat_router, "get_rds_data_client", lambda: FakeDb(
        clock, {"FROM trip_packages": 40},
    ))
    _products, activities, _message = asyncio.run(chat_router.retrieval_availability_search(
        "Which trip lengths are still available?", package_id="AML-002",
    ))
    assert _ms(_span(activities, "PackageAgent: Finding package")) == 40
    assert _ms(_span(activities, "PackageAgent: Duration inventory verified")) is None


def test_search_agent_times_the_lexical_and_hydration_queries(clock, monkeypatch):
    agent = SearchAgent.__new__(SearchAgent)
    spans: list = []
    agent.activity_callback = spans.append
    agent.db = FakeDb(clock, {"semantic_trip_search": 120, "ts_rank": 30, "WHERE package_id IN": 25})
    agent.embedding_service = FakeEmbeddings(clock)
    asyncio.run(agent.hybrid_search("quiet villa"))

    assert _ms(_span(spans, "Lexical candidates merged")) == 30
    assert _ms(_span(spans, "Catalog card details hydrated")) == 25


def test_production_hydration_times_the_catalog_query(clock):
    agent = ProductionAgent.__new__(ProductionAgent)
    spans: list = []
    agent.activity_callback = spans.append
    agent.db = FakeDb(clock, {"FROM trip_packages": 65})
    asyncio.run(agent._hydrate([{"package_id": "AML-002"}]))
    assert _ms(_span(spans, "Hydrated managed search results")) == 65


# -------------------------------------------------------------- AgentCore Runtime

def _decision() -> RuntimeDecision:
    return RuntimeDecision(
        runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/rt-1",
        runtime_session_id="rt-session", qualifier="DEFAULT", message="",
        recommended_package_ids=[], follow_ups=[],
    )


def test_runtime_turn_span_carries_the_runtime_measurement():
    decision = _decision()
    _apply_result(decision, {"message": "Done.", "elapsed_ms": 16827})
    agent = ProductionAgent.__new__(ProductionAgent)
    spans: list = []
    agent.activity_callback = spans.append
    agent._runtime_span(decision)
    assert _ms(_span(spans, "AgentCore Runtime · turn complete")) == 16827


def test_runtime_turn_span_has_no_number_when_the_runtime_reports_none():
    decision = _decision()
    _apply_result(decision, {"message": "Done."})
    agent = ProductionAgent.__new__(ProductionAgent)
    spans: list = []
    agent.activity_callback = spans.append
    agent._runtime_span(decision)
    span = _span(spans, "AgentCore Runtime · turn complete")
    assert _ms(span) is None
    assert "None" not in span.details and " 0 ms" not in span.details


# ------------------------------------------------------------ Aurora checkpoints

class FakeCheckpointClient:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock

    async def execute(self, _sql, _params=None):
        self.clock.spend(53)
        return []


def test_saver_times_each_put_and_the_workflow_attaches_it_to_its_checkpoint_span(clock):
    saver = AuroraDataApiSaver(FakeCheckpointClient(clock))
    node = {"id": "node-span", "title": "Workflow node: search", "execution_time_ms": 956}
    checkpoint = {"id": "checkpoint-span", "title": "Checkpoint · AuroraDataApiSaver.put",
                  "execution_time_ms": None}
    asyncio.run(saver.aput(
        {"configurable": {"thread_id": "t1", "checkpoint_ns": "", "checkpoint_id": None}},
        {"id": "cp-1", "channel_values": {"activities": [node, checkpoint]}},
        {"step": 1},
        {"activities": "1"},
    ))
    # One blob and one checkpoint row, 53 ms each, measured as one put.
    assert saver.put_timings == {"checkpoint-span": 106}

    timed = _with_checkpoint_timings([node, checkpoint], saver.put_timings)
    assert _ms(timed[1]) == 106
    assert _ms(timed[0]) == 956
    assert saver.put_timings == {}


def test_checkpoint_spans_keep_no_number_when_the_saver_measures_nothing():
    checkpoint = {"id": "c", "title": "Checkpoint · MemorySaver.put", "execution_time_ms": None}
    assert _with_checkpoint_timings([checkpoint], None) == [checkpoint]
