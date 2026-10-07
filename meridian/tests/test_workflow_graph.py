"""The Strands Graph routes like the LangGraph workflow, pauses for review, and resumes.

A fresh graph instance over the same storage stands in for a restarted process:
nothing is shared between the two but what the storage holds.
"""

import json
from unittest.mock import AsyncMock

import pytest
from strands.session import SnapshotSessionManager
from strands.storage import InMemoryStorage

from backend.agents.phase_05_workflow.governed_hold import HoldOutcomeUnknown
from backend.agents.phase_05_workflow.graph import (
    CONFIRM_INTERRUPT,
    REVIEW_INTERRUPT,
    RunContext,
    build_graph,
    fold_state,
    next_nodes,
    pending_interrupts,
    run_task,
    snapshot_key,
)
from backend.agents.phase_05_workflow.nodes import WorkflowNodes
from backend.demo_prompts import PROMPT_LADDER
from tests.phase5_support import GatewayFake, fake_availability, fake_search

CANONICAL = PROMPT_LADDER[5].works[0]
RECOVERY_WORDINGS = [
    CANONICAL,
    "My flight was canceled. Rework my Tokyo trip and check availability.",
    "My JFK flight was cancelled, rework it: which dates are available for Tokyo?",
    "Flight cancelled. Rebuild my plan with the open dates for Tokyo.",
]


class Harness:
    """Build graphs over one storage, the way separate processes would."""

    def __init__(self):
        self.storage = InMemoryStorage()
        self.gateway = GatewayFake()

    def graph(self, thread, *, confirmed=False, review=False, pause_after=None, nodes=None):
        nodes = nodes or WorkflowNodes(fake_search, fake_availability, gateway_call=self.gateway)
        nodes._prepare_governed_hold = AsyncMock(return_value=("jrn_test", 400000))
        nodes._booking_status = AsyncMock(return_value=None)
        run = RunContext(
            thread_id=thread,
            execution_id="exe_1",
            traveler_confirmed=confirmed,
            pause_after_search=review,
            pause_after=pause_after,
        )
        session = SnapshotSessionManager(
            thread, storage=self.storage, multi_agent_save_latest_on="node"
        )
        return build_graph(nodes, run, session)

    async def snapshot(self, thread):
        raw = await self.storage.read(snapshot_key(thread))
        return json.loads(raw) if raw else None


def task(query, thread="t1"):
    return run_task(
        query=query, traveler_id="trv_x", conversation_id=thread,
        journey_id="jrn_test", travelers_count=2,
    )


def answers_for(snapshot):
    return [
        {"interruptResponse": {"interruptId": i["id"], "response": "approved"}}
        for i in pending_interrupts(snapshot)
    ]


@pytest.mark.parametrize("query, route", [
    ("Find me a romantic trip to Paris", ["classify", "search", "synthesize"]),
    ("What dates are available for Tokyo?", ["classify", "availability", "synthesize"]),
    (
        "Recall my October Tokyo plan and saved preferences",
        ["classify", "memory_recall", "synthesize"],
    ),
    (
        "Find a Tokyo trip and check availability",
        ["classify", "search", "availability", "synthesize"],
    ),
])
async def test_non_recovery_routes_match_the_langgraph_workflow(query, route):
    harness = Harness()
    nodes = WorkflowNodes(fake_search, fake_availability, AsyncMock(return_value=([], [])))
    graph = harness.graph("t1", nodes=nodes)
    result = await graph.invoke_async(task(query))
    assert result.status.value == "completed"
    assert [node.node_id for node in result.execution_order] == route
    assert harness.gateway.calls == []


@pytest.mark.parametrize("review", [False, True])
@pytest.mark.parametrize("query", RECOVERY_WORDINGS)
async def test_no_hold_without_jordans_answered_review(query, review):
    """Named owner requirement (2026-10-06): a fresh run never reaches the hold."""
    harness = Harness()
    result = await harness.graph("t1", review=review).invoke_async(task(query))
    snapshot = await harness.snapshot("t1")
    assert result.status.value == "interrupted"
    assert harness.gateway.calls == []
    assert set(next_nodes(snapshot)) <= {"availability", "prepare_hold"}
    names = {i["name"] for i in pending_interrupts(snapshot)}
    assert names <= {REVIEW_INTERRUPT, CONFIRM_INTERRUPT}


async def test_the_canonical_recovery_pauses_after_search():
    harness = Harness()
    await harness.graph("t1").invoke_async(task(CANONICAL))
    snapshot = await harness.snapshot("t1")
    assert next_nodes(snapshot) == ["availability"]
    assert [i["name"] for i in pending_interrupts(snapshot)] == [REVIEW_INTERRUPT]


async def test_a_new_graph_resumes_from_the_snapshot_and_holds_once():
    harness = Harness()
    await harness.graph("t1").invoke_async(task(CANONICAL))
    answers = answers_for(await harness.snapshot("t1"))

    resumed = harness.graph("t1", confirmed=True)
    result = await resumed.invoke_async(answers)

    order = [node.node_id for node in result.execution_order]
    assert order == ["classify", "search", "availability", "prepare_hold", "hold", "synthesize"]
    assert [call["travelerConfirmed"] for call in harness.gateway.calls] == [True]
    state = fold_state(resumed.state)
    assert state["hold_id"] == harness.gateway.calls[0]["bookingId"]
    assert state["travelers_count"] == 2


async def test_a_lost_hold_reply_replays_the_same_intent_in_a_new_graph():
    harness = Harness()
    await harness.graph("t1").invoke_async(task(RECOVERY_WORDINGS[1]))
    answers = answers_for(await harness.snapshot("t1"))

    def lose_reply(name, arguments):
        harness.gateway(name, arguments)
        raise TimeoutError("reply lost after the business write")

    failing = WorkflowNodes(fake_search, fake_availability, gateway_call=lose_reply)
    with pytest.raises(HoldOutcomeUnknown, match="hold outcome is unknown"):
        await harness.graph("t1", confirmed=True, nodes=failing).invoke_async(answers)
    after_loss = await harness.snapshot("t1")
    assert next_nodes(after_loss) == ["hold"]

    replay = harness.graph("t1", confirmed=True)
    result = await replay.invoke_async(after_loss["data"]["state"]["current_task"])
    assert "synthesize" in {node.node_id for node in result.execution_order}
    request_ids = {call["holdRequestId"] for call in harness.gateway.calls}
    assert len(harness.gateway.calls) == 2 and len(request_ids) == 1
    assert fold_state(replay.state)["hold_id"] == harness.gateway.calls[0]["bookingId"]


async def test_pause_after_a_named_node_is_answerable_on_resume():
    harness = Harness()
    await harness.graph("t1").invoke_async(task(RECOVERY_WORDINGS[1]))
    first = answers_for(await harness.snapshot("t1"))
    paused = await harness.graph("t1", confirmed=True, pause_after="hold").invoke_async(first)
    assert paused.status.value == "interrupted"
    assert next_nodes(await harness.snapshot("t1")) == ["synthesize"]

    second = answers_for(await harness.snapshot("t1"))
    done = await harness.graph("t1", confirmed=True).invoke_async(second)
    assert done.status.value == "completed"
    assert len(harness.gateway.calls) == 1
