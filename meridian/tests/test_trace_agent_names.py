"""A step is called an agent only when a model runs its loop; code steps say they are code."""

import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import backend.routers.chat as chat_router
from backend.agents.phase_04_production import concierge as concierge_mod
from backend.agents.phase_05_workflow import memory_recall
from backend.agents.phase_05_workflow.nodes import WorkflowNodes
from backend.retrieval import availability
from backend.tools import catalog_tools
from backend.trace_names import (
    AVAILABILITY_CODE, MCP_CODE, MEMORY_CODE, PRODUCTION_CODE, SQL_CODE,
    WORKFLOW_CODE,
)
from tests.phase5_support import GatewayFake, fake_availability, fake_search
from tests.test_production_transaction_boundaries import build_agent, runtime_decision

MERIDIAN = Path(__file__).resolve().parents[1]
ROW = {
    "package_id": "AML-002", "name": "Amalfi Coast Villa Week", "operator": "Costa",
    "price_per_person": 1599.0, "description": "Villa", "image_url": "", "trip_type": "Beach",
    "destination": "Positano", "region": "Europe", "durations": ["5 nights"],
    "availability": {"5 nights": 3}, "highlights": [], "similarity": 0.4,
}


class OneRowDb:
    async def execute(self, sql, params=None, transaction_id=None):
        return [dict(ROW)]


def agent_names(activities) -> set[str]:
    return {getattr(a, "agent_name", None) for a in activities}


async def test_phase_1_spans_are_labelled_as_code(monkeypatch):
    monkeypatch.setattr(catalog_tools, "get_rds_data_client", lambda: OneRowDb())
    _products, activities = await chat_router.sql_search("Show me city trips under $2,000")
    assert agent_names(activities) == {SQL_CODE}


async def test_phase_2_spans_are_labelled_as_code(monkeypatch):
    class Client:
        tools = [{"name": "search_trips"}]

        async def call(self, tool, arguments):
            return await catalog_tools.search_trips(**arguments)

    @asynccontextmanager
    async def session():
        yield Client()

    monkeypatch.setattr(catalog_tools, "get_rds_data_client", lambda: OneRowDb())
    monkeypatch.setattr(chat_router, "concierge_mcp_session", session)
    _products, activities, _text = await chat_router.mcp_search(
        "Show me city trips under $2,000", traveler_id="trv_meridian_demo")
    assert agent_names(activities) == {MCP_CODE}


async def test_the_availability_check_is_labelled_as_code(monkeypatch):
    monkeypatch.setattr(availability, "get_rds_data_client", lambda: OneRowDb())
    _products, activities, _message = await availability.retrieval_availability_search(
        "Which trip lengths are still available?", package_id="AML-002")
    assert agent_names(activities) == {AVAILABILITY_CODE}


async def test_the_workflow_memory_branch_is_labelled_as_code(monkeypatch):
    class Store:
        def __init__(self):
            self.db = SimpleNamespace(scoped_session=self.scoped)

        @asynccontextmanager
        async def scoped(self, **_kwargs):
            yield "tx"

        async def recall_preferences(self, traveler_id, *, transaction_id):
            return []

        async def recall_short_term(self, conversation_id, *, limit, transaction_id):
            return []

        def prepare_embedding_vector(self, _text, *, input_type):
            return "[0.1]"

        async def recall_similar_interactions(
            self, traveler_id, query, *, limit, transaction_id, query_vector, embedding_prepared
        ):
            return []

    async def search(query, limit=5):
        return [], []

    monkeypatch.setattr("backend.memory.store.get_memory_store", lambda: Store())
    monkeypatch.setattr(memory_recall, "get_agentcore_identity",
                        lambda: SimpleNamespace(authorization_context=lambda: {}))
    monkeypatch.setattr(memory_recall, "retrieval_search", search)
    _products, activities = await memory_recall.workflow_memory_recall(
        "Recall my plan", traveler_id="trv_x", conversation_id="t1")
    assert agent_names(activities) == {MEMORY_CODE}


def test_the_phase_4_orchestrator_is_labelled_as_code(monkeypatch):
    agent = build_agent([], runtime_decision(), [])
    monkeypatch.setattr(concierge_mod, "require_agentcore_platform", lambda **_kw: None)
    _packages, activities, *_ = asyncio.run(
        agent.process_turn("Plan Tokyo", "trv_meridian_demo", None, 5))
    grant = next(a for a in activities if a.title == "Workload traveler grant allowed")
    assert grant.agent_name == PRODUCTION_CODE


async def test_workflow_graph_nodes_are_labelled_as_code():
    gateway = GatewayFake()
    nodes = WorkflowNodes(fake_search, fake_availability, gateway_call=gateway)
    nodes._prepare_governed_hold = AsyncMock(return_value=("jrn_test", 400000))
    nodes._committed_hold = gateway.committed_hold
    nodes._booking_status = AsyncMock(return_value=None)
    nodes.memory_recall_fn = None
    config = {"configurable": {"thread_id": "t1", "execution_id": "exe_1",
                                "traveler_confirmed": True}}
    state = {"query": "Rework my Tokyo trip and check availability.", "traveler_id": "trv_x",
             "conversation_id": "t1", "travelers_count": 2, "activities": []}
    for step in ("classify", "search", "availability", "prepare_hold", "hold", "memory_recall",
                 "synthesize"):
        node = getattr(nodes, step)
        out = await (node(state, config) if step == "hold" else node(state))
        state = {**state, **out}
    names = {span["agent_name"] for span in state["activities"]}
    assert len(state["activities"]) >= 7
    assert names <= {WORKFLOW_CODE, AVAILABILITY_CODE, MEMORY_CODE}


async def test_the_workflow_search_node_names_the_gateway_search_tool():
    nodes = WorkflowNodes(fake_search, fake_availability, gateway_call=GatewayFake())
    out = await nodes.search({"query": "Rework my Tokyo trip.", "activities": []})

    (node,) = [span for span in out["activities"] if span["title"] == "Workflow node: search"]
    assert node["telemetry"]["component"] == "Strands Graph → AgentCore Gateway: hybrid_trip_search"


async def test_the_workflow_availability_nodes_name_the_availability_code_step():
    nodes = WorkflowNodes(fake_search, fake_availability, gateway_call=GatewayFake())
    prior = [{"product_id": f"pkg_{i}", "name": f"Trip {i}"} for i in range(3)]
    states = [
        {"query": "Is Tokyo available?", "intent": "availability", "activities": []},
        {"query": "Rework my Tokyo trip.", "intent": "plan", "packages": prior, "activities": []},
    ]
    components = []
    for state in states:
        out = await nodes.availability(state)
        (node,) = [span for span in out["activities"]
                   if span["title"].startswith("Workflow node: availability")]
        components.append(node["telemetry"]["component"])

    assert components == [
        f"Strands Graph → {AVAILABILITY_CODE}",
        f"Strands Graph → {AVAILABILITY_CODE} fan-out",
    ]


# The audit row's `agent_name="ProductionAgent"` in concierge.py is a stored identifier that
# older rows share, so that file is covered by the behavior test above instead.
FORBIDDEN = {
    "backend/routers/chat.py": (
        "SQLAgent", "MCPAgent", "PackageAgent", "OrchestrationAgent", "ProductionAgent",
        "Concierge",
    ),
    "backend/retrieval/availability.py": ("PackageAgent",),
    "backend/agents/phase_05_workflow/nodes.py": ("PackageAgent", "SearchAgent"),
    "backend/agents/phase_05_workflow/governed_tools.py": ("SearchAgent", "PackageAgent"),
    "backend/agents/phase_05_workflow/memory_recall.py": ("MemoryAgent",),
    "backend/agents/phase_05_workflow/state.py": ("OrchestrationAgent",),
    "backend/agents/phase_04_production/memory_agent.py": ("MemoryAgent",),
}


@pytest.mark.parametrize("path", sorted(FORBIDDEN))
def test_no_code_step_still_carries_an_agent_name(path):
    names = "|".join(FORBIDDEN[path])
    pattern = rf'agent_name\s*(?::\s*str\s*)?=\s*"({names})"'
    found = re.findall(pattern, (MERIDIAN / path).read_text())
    assert not found, f"{path} still names code steps as agents: {found}"
