"""No courtesy hold without the traveler's answered review.

The Cedar policy permits a hold only when ``travelerConfirmed`` is true, so that
flag must mean what it says: the traveler resumed the workflow after reviewing
the plan. These tests drive the real compiled graph; only the two external
boundaries are replaced (the Gateway transport, and the Aurora reads that size
the budget), so nothing is reserved.
"""
import json
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import MemorySaver

import backend.agents.phase_05_workflow.workflow as module
from backend.agents.phase_05_workflow.governed_hold import HOLD_TOOL
from backend.agents.phase_05_workflow.workflow import CheckpointBackend, OrchestrationAgent
from backend.demo_prompts import PROMPT_LADDER

CANONICAL_RECOVERY = PROMPT_LADDER[5].works[0]
RECOVERY_WORDINGS = [
    CANONICAL_RECOVERY,
    "My flight was canceled. Rework my Tokyo trip and check availability.",
    # Classifies as "availability": the route never visits the search node.
    "My JFK flight was cancelled, rework it: which dates are available for Tokyo?",
    "Flight cancelled. Rebuild my plan with the open dates for Tokyo.",
]


@pytest.fixture
def gateway_calls():
    return []


@pytest.fixture
def workflow_factory(monkeypatch, gateway_calls):
    backend = CheckpointBackend(MemorySaver(), "MemorySaver (in-process)", False)

    async def initialize():
        return backend

    monkeypatch.setattr(module, "initialize_checkpoint_backend", initialize)
    monkeypatch.delenv("LANGGRAPH_DEMO_INTERRUPT_AFTER", raising=False)

    async def search(query, limit=5):
        return ([{"product_id": "pkg_tokyo", "name": "Tokyo", "price": 100,
                  "available_sizes": ["7 nights"], "availability": {"7 nights": 10}}], [])

    async def availability(query, package_id=None):
        return ([{"product_id": package_id or "pkg_tokyo", "available_sizes": ["7 nights"],
                  "availability": {"7 nights": 10}}], [], "")

    def gateway(tool, arguments):
        gateway_calls.append((tool, arguments))
        receipt = {
            "bookingId": arguments["bookingId"], "status": "held",
            "expiresAt": "2026-10-06T20:15:00Z", "createdAt": "2026-10-06T20:00:00Z",
            "observedAt": "2026-10-06T20:00:01Z",
        }
        return {"result": {"content": [{"type": "text", "text": json.dumps({"hold": receipt})}]}}

    def build(*, review_only=False):
        workflow = OrchestrationAgent(
            search_fn=search, availability_fn=availability, review_only=review_only,
        )
        workflow._prepare_governed_hold = AsyncMock(return_value=("jrn_test", 400000))
        workflow._gateway_call = gateway
        return workflow

    return build


@pytest.mark.asyncio
@pytest.mark.parametrize("review_only", [False, True])
@pytest.mark.parametrize("prompt", RECOVERY_WORDINGS)
async def test_a_fresh_recovery_run_pauses_before_any_hold(
    workflow_factory, gateway_calls, prompt, review_only
):
    workflow = workflow_factory(review_only=review_only)
    result = await workflow.run(prompt, "trv_alice", "fresh-recovery")

    saved = await workflow.graph.aget_state({"configurable": {"thread_id": "fresh-recovery"}})
    assert result["workflow_status"] == "paused"
    assert set(saved.next) <= {"availability", "prepare_hold"}
    assert gateway_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", RECOVERY_WORDINGS)
async def test_only_the_travelers_resume_confirms_the_hold(workflow_factory, gateway_calls, prompt):
    await workflow_factory().run(prompt, "trv_alice", "reviewed-recovery")
    assert gateway_calls == []

    resumed = await workflow_factory().run(
        "Resume workflow from checkpoint", "trv_alice", "reviewed-recovery", resume=True,
    )

    assert [tool for tool, _ in gateway_calls] == [HOLD_TOOL]
    assert gateway_calls[0][1]["travelerConfirmed"] is True
    assert resumed["hold_id"] == gateway_calls[0][1]["bookingId"]


@pytest.mark.asyncio
async def test_the_hold_node_never_confirms_on_its_own(workflow_factory, gateway_calls):
    workflow = workflow_factory()
    state = {
        "traveler_id": "trv_alice",
        "conversation_id": "direct-node",
        "travelers_count": 1,
        "packages": [{"product_id": "pkg_tokyo", "price": 100,
                      "available_sizes": ["7 nights"], "availability": {"7 nights": 10}}],
    }

    await workflow._node_hold(state, {"configurable": {"thread_id": "direct-node"}})

    assert gateway_calls[0][1]["travelerConfirmed"] is False
