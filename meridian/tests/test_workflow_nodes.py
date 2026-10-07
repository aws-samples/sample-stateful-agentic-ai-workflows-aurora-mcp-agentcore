"""The ported steps keep the LangGraph nodes' outputs, spans and hold arguments."""

from unittest.mock import AsyncMock

import pytest

from backend.agents.phase_05_workflow.governed_hold import HOLD_TOOL
from backend.agents.phase_05_workflow.nodes import WorkflowNodes
from tests.phase5_support import GatewayFake, fake_availability, fake_search

PLAN = "My flight was canceled. Rework my Tokyo trip and check availability."


@pytest.fixture
def gateway():
    return GatewayFake()


@pytest.fixture
def nodes(gateway):
    built = WorkflowNodes(fake_search, fake_availability, gateway_call=gateway)
    built._prepare_governed_hold = AsyncMock(return_value=("jrn_test", 400000))
    built._booking_status = AsyncMock(return_value=None)
    return built


async def test_classify_records_the_intent_with_the_strands_component(nodes):
    out = await nodes.classify({"query": PLAN, "activities": []})
    assert out["intent"] == "plan"
    span = out["activities"][-1]
    assert span["title"] == "Workflow node: classify → plan"
    assert span["telemetry"]["component"] == "Strands Graph"


async def test_search_saves_packages_as_plain_dicts_and_announces_the_snapshot(nodes):
    out = await nodes.search({"query": PLAN, "activities": []})
    assert out["packages"][0]["product_id"] == "pkg_tokyo"
    titles = [span["title"] for span in out["activities"]]
    assert titles[0] == "Workflow node: search"
    assert titles[-1].startswith("Checkpoint · ")
    fields = {f["label"]: f["value"] for f in out["activities"][-1]["telemetry"]["fields"]}
    assert fields["checkpoint_durable"] == "true"
    assert fields["checkpoint_store"] == "workflow_snapshots"


async def test_memory_recall_returns_json_ready_packages(nodes):
    product = type("Product", (), {"model_dump": lambda self, **kwargs: {"product_id": "pkg_x"}})()
    nodes.memory_recall_fn = AsyncMock(return_value=([product], []))
    out = await nodes.memory_recall({"query": "Recall my trip", "activities": []})
    assert out["packages"] == [{"product_id": "pkg_x"}]


async def test_the_hold_sends_confirmation_only_when_the_run_carries_it(nodes, gateway):
    state = {"traveler_id": "trv_x", "conversation_id": "t1", "travelers_count": 2,
             "packages": [{"product_id": "pkg_tokyo", "price": 100,
                           "available_sizes": ["7 nights"], "availability": {"7 nights": 10}}],
             "activities": []}
    await nodes.hold(state, {"configurable": {"thread_id": "t1", "execution_id": "exe_1"}})
    await nodes.hold(state, {"configurable": {"thread_id": "t1", "execution_id": "exe_1",
                                              "traveler_confirmed": True}})
    assert [call["travelerConfirmed"] for call in gateway.calls] == [False, True]
    assert {call["tool"] for call in gateway.calls} == {HOLD_TOOL}
    assert gateway.calls[0]["executionId"] == "exe_1"


async def test_synthesize_reports_the_booking_status_aurora_holds(nodes):
    nodes._booking_status = AsyncMock(return_value="released")
    out = await nodes.synthesize({"query": PLAN, "intent": "plan", "packages": [{"product_id": "p"}],
                                  "availability_checks": 1, "hold_id": "hold_1",
                                  "hold_status": "held", "traveler_id": "trv_x", "activities": []})
    assert "was released after a step failed" in out["response"]
    assert "each step saved to Aurora" in out["response"]
    nodes._booking_status.assert_awaited_once_with("trv_x", "hold_1")
