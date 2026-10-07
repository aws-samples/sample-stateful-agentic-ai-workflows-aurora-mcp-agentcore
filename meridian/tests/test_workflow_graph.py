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
    ResumableStorage,
    RunContext,
    build_graph,
    fold_snapshot,
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

    def __init__(self, storage=None):
        self.storage = storage or InMemoryStorage()
        self.gateway = GatewayFake()

    def graph(
        self, thread, *, confirmed=False, review=False, pause_after=None, nodes=None,
        storage=None,
    ):
        """A new graph; ``storage`` overrides the runner's ``ResumableStorage`` wrapping."""
        nodes = nodes or WorkflowNodes(fake_search, fake_availability, gateway_call=self.gateway)
        nodes._prepare_governed_hold = AsyncMock(return_value=("jrn_test", 400000))
        nodes._booking_status = AsyncMock(return_value=None)
        run = RunContext(
            thread_id=thread,
            execution_id="exe_1",
            traveler_confirmed=confirmed,
            review_requested=review,
            pause_after=pause_after,
        )
        session = SnapshotSessionManager(
            thread,
            storage=storage or ResumableStorage(self.storage),
            multi_agent_save_latest_on="node",
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
    nodes = WorkflowNodes(
        fake_search, fake_availability, AsyncMock(return_value=([], [])),
        gateway_call=harness.gateway,
    )
    graph = harness.graph("t1", nodes=nodes)
    result = await graph.invoke_async(task(query))
    assert result.status.value == "completed"
    assert [node.node_id for node in result.execution_order] == route
    assert harness.gateway.calls == []


REVIEW_PAUSE = ("availability", REVIEW_INTERRUPT)
CONFIRM_PAUSE = ("prepare_hold", CONFIRM_INTERRUPT)


@pytest.mark.parametrize("query, review, pause", [
    (CANONICAL, False, REVIEW_PAUSE),
    (CANONICAL, True, REVIEW_PAUSE),
    (RECOVERY_WORDINGS[1], False, CONFIRM_PAUSE),
    (RECOVERY_WORDINGS[1], True, REVIEW_PAUSE),
    # An availability question runs no search, so review has nothing to pause after.
    (RECOVERY_WORDINGS[2], False, CONFIRM_PAUSE),
    (RECOVERY_WORDINGS[2], True, CONFIRM_PAUSE),
    (RECOVERY_WORDINGS[3], False, CONFIRM_PAUSE),
    (RECOVERY_WORDINGS[3], True, REVIEW_PAUSE),
])
async def test_no_hold_without_jordans_answered_review(query, review, pause):
    """Named owner requirement (2026-10-06): a fresh run never reaches the hold."""
    harness = Harness()
    result = await harness.graph("t1", review=review).invoke_async(task(query))
    snapshot = await harness.snapshot("t1")
    assert result.status.value == "interrupted"
    assert snapshot is not None
    paused_at = (next_nodes(snapshot), [i["name"] for i in pending_interrupts(snapshot)])
    assert paused_at == ([pause[0]], [pause[1]])
    assert "prepare_hold" not in [node.node_id for node in result.execution_order]
    assert "prepare_hold" not in snapshot["data"]["state"]["execution_order"]
    assert harness.gateway.calls == []


async def test_review_pauses_a_search_only_route_before_synthesize():
    harness = Harness()
    await harness.graph("t1", review=True).invoke_async(task("Find me a romantic trip to Paris"))
    snapshot = await harness.snapshot("t1")
    assert next_nodes(snapshot) == ["synthesize"]
    assert [i["name"] for i in pending_interrupts(snapshot)] == [REVIEW_INTERRUPT]

    resumed = harness.graph("t1", confirmed=True, review=True)
    result = await resumed.invoke_async(answers_for(snapshot))
    assert result.status.value == "completed"
    assert [node.node_id for node in result.execution_order] == [
        "classify", "search", "synthesize"
    ]


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


@pytest.mark.parametrize("query, following", [
    ("Find me a romantic trip to Paris", "synthesize"),
    ("Find a Tokyo trip and check availability", "availability"),
])
async def test_pause_after_search_stops_before_whichever_node_runs_next(query, following):
    harness = Harness()
    result = await harness.graph("t1", pause_after="search").invoke_async(task(query))
    snapshot = await harness.snapshot("t1")
    assert result.status.value == "interrupted"
    assert next_nodes(snapshot) == [following]
    assert [i["name"] for i in pending_interrupts(snapshot)] == ["pause_after_search"]


@pytest.mark.parametrize("pause_after", ["serach", "synthesize"])
def test_pause_after_must_name_a_node_that_another_follows(pause_after):
    with pytest.raises(ValueError, match="would never pause the run"):
        RunContext(
            thread_id="t1", execution_id=None, traveler_confirmed=False,
            review_requested=False, pause_after=pause_after,
        )


async def test_the_persisted_snapshot_folds_to_the_live_state():
    harness = Harness()
    await harness.graph("t1").invoke_async(task(CANONICAL))
    resumed = harness.graph("t1", confirmed=True)
    result = await resumed.invoke_async(answers_for(await harness.snapshot("t1")))
    assert result.status.value == "completed"

    persisted = fold_snapshot(await harness.snapshot("t1"))
    assert persisted == fold_state(resumed.state)
    assert persisted["hold_id"] and persisted["activities"]


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


class RecordingStorage:
    """``InMemoryStorage`` that keeps every write, in order, as a crash would find it."""

    def __init__(self):
        self.inner = InMemoryStorage()
        self.writes = []

    async def write(self, key, data):
        self.writes.append((key, data))
        await self.inner.write(key, data)

    async def read(self, key):
        return await self.inner.read(key)

    async def delete(self, key):
        await self.inner.delete(key)

    async def list(self, query=""):
        return await self.inner.list(query)


def interrupt_state_of(snapshot):
    return snapshot["data"]["state"]["_internal_state"]["interrupt_state"]


async def storage_holding(key, data):
    storage = InMemoryStorage()
    await storage.write(key, data)
    return storage


async def test_a_worker_killed_inside_hold_after_the_answer_resumes_the_hold():
    """The last snapshot before the Gateway call is what a SIGKILL inside ``hold`` leaves."""
    recording = RecordingStorage()
    harness = Harness(storage=recording)
    writes_at_gateway_call = []

    def gateway(name, arguments):
        writes_at_gateway_call.append(len(recording.writes))
        return harness.gateway(name, arguments)

    await harness.graph("t1").invoke_async(task(RECOVERY_WORDINGS[1]))
    answers = answers_for(await harness.snapshot("t1"))
    assert [i["name"] for i in pending_interrupts(await harness.snapshot("t1"))] == [
        CONFIRM_INTERRUPT
    ]
    nodes = WorkflowNodes(fake_search, fake_availability, gateway_call=gateway)
    await harness.graph("t1", confirmed=True, nodes=nodes).invoke_async(answers)

    key = snapshot_key("t1")
    saved = [data for written_key, data in recording.writes if written_key == key]
    saved_before_call = [
        data for written_key, data in recording.writes[: writes_at_gateway_call[0]]
        if written_key == key
    ]
    crashed = saved_before_call[-1]
    one_node_later = json.loads(saved[len(saved_before_call)])
    snapshot = json.loads(crashed)
    assert interrupt_state_of(snapshot)["activated"] is True
    assert interrupt_state_of(snapshot)["interrupts"]
    assert pending_interrupts(snapshot) == []
    assert next_nodes(snapshot) == ["hold"]
    current_task = snapshot["data"]["state"]["current_task"]

    unwrapped = await storage_holding(key, crashed)
    with pytest.raises(TypeError, match="must resume from interrupt"):
        await harness.graph("t1", confirmed=True, storage=unwrapped).invoke_async(current_task)

    wrapped = ResumableStorage(await storage_holding(key, crashed))
    rewritten = json.loads(await wrapped.read(key))
    assert interrupt_state_of(rewritten) == interrupt_state_of(one_node_later) == {
        "interrupts": {}, "context": {}, "activated": False,
    }
    replay = harness.graph("t1", confirmed=True, storage=wrapped)
    result = await replay.invoke_async(current_task)

    order = [node.node_id for node in result.execution_order]
    assert order.count("hold") == 1 and "synthesize" in order
    assert len(harness.gateway.calls) == 2
    assert len({call["holdRequestId"] for call in harness.gateway.calls}) == 1


async def test_a_worker_killed_inside_prepare_hold_after_the_review_resumes_it():
    """The last snapshot without ``prepare_hold`` is what a SIGKILL inside it leaves."""
    recording = RecordingStorage()
    harness = Harness(storage=recording)
    await harness.graph("t1").invoke_async(task(CANONICAL))
    answers = answers_for(await harness.snapshot("t1"))
    await harness.graph("t1", confirmed=True).invoke_async(answers)

    key = snapshot_key("t1")
    crashed = [
        data for written_key, data in recording.writes
        if written_key == key
        and "prepare_hold" not in json.loads(data)["data"]["state"]["execution_order"]
    ][-1]
    snapshot = json.loads(crashed)
    assert interrupt_state_of(snapshot)["activated"] is True
    assert pending_interrupts(snapshot) == []
    assert next_nodes(snapshot) == ["prepare_hold"]

    wrapped = ResumableStorage(await storage_holding(key, crashed))
    replay = harness.graph("t1", confirmed=True, storage=wrapped)
    result = await replay.invoke_async(snapshot["data"]["state"]["current_task"])
    order = [node.node_id for node in result.execution_order]
    assert order == ["classify", "search", "availability", "prepare_hold", "hold", "synthesize"]
    assert len(harness.gateway.calls) == 2


async def test_resumable_storage_returns_other_bytes_unchanged():
    harness = Harness()
    await harness.graph("t1").invoke_async(task(RECOVERY_WORDINGS[1]))
    paused = await harness.storage.read(snapshot_key("t1"))
    assert pending_interrupts(json.loads(paused))
    blobs = {"blob/binary": b"\xff not json", "blob/agent": b'{"scope": "agent"}'}
    for key, data in blobs.items():
        await harness.storage.write(key, data)

    wrapped = ResumableStorage(harness.storage)
    assert await wrapped.read(snapshot_key("t1")) == paused
    for key, data in blobs.items():
        assert await wrapped.read(key) == data
    assert await wrapped.read("blob/missing") is None
