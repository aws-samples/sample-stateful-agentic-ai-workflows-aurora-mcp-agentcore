"""Real graph regressions with an isolated saver and no live business actions."""
import json
from unittest.mock import AsyncMock

import pytest
from langgraph.checkpoint.memory import MemorySaver

import backend.agents.phase_05_workflow.workflow as module
from backend.agents.phase_05_workflow.workflow import (
    CheckpointBackend, OrchestrationAgent, WorkflowAuthorizationError,
)


@pytest.fixture
def workflow_factory(monkeypatch):
    backend = CheckpointBackend(MemorySaver(), "MemorySaver (in-process)", False)

    async def initialize():
        return backend

    monkeypatch.setattr(module, "initialize_checkpoint_backend", initialize)
    monkeypatch.delenv("LANGGRAPH_DEMO_INTERRUPT_AFTER", raising=False)

    async def search(query, limit=5):
        name = "Tokyo" if "Tokyo" in query else "Paris"
        return ([{"product_id": name, "name": name, "price": 100,
                  "available_sizes": ["2 nights"], "availability": {"2 nights": 10}}], [])

    async def availability(query, package_id=None):
        return ([{"product_id": package_id, "available_sizes": ["2 nights"],
                  "availability": {"2 nights": 10}}], [], "")

    return lambda: OrchestrationAgent(search_fn=search, availability_fn=availability)


RECOVERY = "My flight was canceled. Rework my Tokyo trip and check availability."


async def reviewed(workflow, query, traveler, thread, **kwargs):
    """Pause for the traveler's review, then resume: the only route to a hold."""
    paused = await workflow.run(query, traveler, thread, **kwargs)
    assert paused["workflow_status"] == "paused"
    return await workflow.run(query, traveler, thread, resume=True)


@pytest.mark.asyncio
async def test_new_turn_cannot_replace_another_travelers_thread(workflow_factory):
    first = workflow_factory()
    await first.run("Find Tokyo trips", "alice", "owned-thread")
    second = workflow_factory()
    with pytest.raises(WorkflowAuthorizationError, match="another traveler"):
        await second.run("Find Paris trips", "bob", "owned-thread")
    saved = await first.graph.aget_state({"configurable": {"thread_id": "owned-thread"}})
    assert saved.values["traveler_id"] == "alice"
    assert saved.values["packages"][0]["product_id"] == "Tokyo"


@pytest.mark.asyncio
async def test_owner_can_start_another_turn_on_the_same_thread(workflow_factory):
    first = workflow_factory()
    await first.run("Find Tokyo trips", "alice", "owned-thread")
    result = await workflow_factory().run("Find Paris trips", "alice", "owned-thread")
    assert result["packages"][0]["product_id"] == "Paris"


@pytest.mark.asyncio
async def test_workflow_http_authorization_is_not_converted_to_a_success(monkeypatch):
    from fastapi import HTTPException
    from backend.http_auth import HttpPrincipal
    from backend.routers import chat as router

    async def refused(*args, **kwargs):
        raise HTTPException(403, "Thread belongs to another traveler")

    monkeypatch.setattr(router, "orchestration_workflow", refused)
    with pytest.raises(HTTPException) as caught:
        await router.chat(router.ChatRequest(message="Find Tokyo trips", phase=5),
                          HttpPrincipal("test", "alice", "test"))
    assert caught.value.status_code == 403


@pytest.mark.asyncio
async def test_new_recoveries_have_new_terms_and_booking_ids(workflow_factory):
    held = []

    async def hold(state):
        intent = state["hold_intent"]
        held.append(intent)
        return {"hold_id": intent["booking_id"]}

    for destination in ("Tokyo", "Paris", "Tokyo"):
        workflow = workflow_factory()
        workflow._node_hold = hold
        result = await reviewed(
            workflow,
            f"My flight was canceled. Rework my {destination} trip and check availability.",
            "alice", "recovery-thread",
        )
        assert result["hold_intent"]["package_id"] == destination
    assert [intent["package_id"] for intent in held] == ["Tokyo", "Paris", "Tokyo"]
    assert len({intent["hold_request_id"] for intent in held}) == 3
    assert len({intent["booking_id"] for intent in held}) == 3


@pytest.mark.asyncio
async def test_resume_preserves_the_checkpointed_business_intent(workflow_factory, monkeypatch):
    first = workflow_factory()
    await first.run(RECOVERY, "alice", "paused-thread")
    monkeypatch.setenv("LANGGRAPH_DEMO_INTERRUPT_AFTER", "prepare_hold")
    paused = await first.run(RECOVERY, "alice", "paused-thread", resume=True)
    assert paused["workflow_status"] == "paused"
    assert paused["hold_intent"]
    seen = []

    async def hold(state):
        seen.append(state["hold_intent"])
        return {"hold_id": state["hold_intent"]["booking_id"]}

    monkeypatch.delenv("LANGGRAPH_DEMO_INTERRUPT_AFTER")
    second = workflow_factory()
    second._node_hold = hold
    result = await second.run("Resume workflow from checkpoint", "alice", "paused-thread", resume=True)
    assert seen == [paused["hold_intent"]]
    assert result["hold_id"] == paused["hold_intent"]["booking_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["disconnect", "rpc_error", "unreadable", "empty"])
async def test_lost_hold_response_keeps_the_original_intent_resumable(workflow_factory, failure):
    receipts = {}
    calls = []

    def gateway(tool, arguments):
        calls.append(arguments)
        request_id = arguments["holdRequestId"]
        replayed = request_id in receipts
        receipt = receipts.setdefault(request_id, {
            "bookingId": arguments["bookingId"],
            "status": "held",
            "expiresAt": "2026-09-12T20:15:00Z",
            "createdAt": "2026-09-12T20:00:00Z",
            "observedAt": "2026-09-12T20:00:01Z",
        })
        if not replayed:
            # The business transaction committed; its response never reached
            # the hold node. A completed graph would make this impossible to resume.
            if failure == "disconnect":
                raise TimeoutError("reply lost after business commit")
            if failure == "rpc_error":
                return {"error": {"code": -32000, "message": "Gateway HTTP 503"}}
            return {"result": {"content": [{
                "type": "text", "text": "truncated" if failure == "unreadable" else "{}",
            }]}}
        return {"result": {"content": [{
            "type": "text",
            "text": json.dumps({"hold": {**receipt, "replayed": True}}),
        }]}}

    def worker():
        workflow = workflow_factory()
        workflow._prepare_governed_hold = AsyncMock(return_value=("journey", 200000))
        workflow._gateway_call = gateway
        return workflow

    first = worker()
    await first.run(RECOVERY, "alice", "lost-response-thread", travelers_count=2)
    with pytest.raises(RuntimeError, match="hold outcome is unknown"):
        await first.run(RECOVERY, "alice", "lost-response-thread", resume=True)
    saved = await first.graph.aget_state({
        "configurable": {"thread_id": "lost-response-thread"},
    })
    assert saved.next == ("hold",)
    assert not saved.values.get("hold_id")
    intent = saved.values["hold_intent"]

    second = worker()
    resumed = await second.run(
        "Resume workflow from checkpoint", "alice", "lost-response-thread", resume=True,
    )
    assert len(receipts) == 1
    assert len(calls) == 2
    assert calls[0]["holdRequestId"] == calls[1]["holdRequestId"] == intent["hold_request_id"]
    assert resumed["hold_id"] == intent["booking_id"]
    assert resumed["hold_expires_at"] == receipts[intent["hold_request_id"]]["expiresAt"]


@pytest.mark.asyncio
async def test_resume_uses_checkpointed_party_size(workflow_factory, monkeypatch):
    first = workflow_factory()
    await first.run(RECOVERY, "alice", "party-thread", travelers_count=3)
    monkeypatch.setenv("LANGGRAPH_DEMO_INTERRUPT_AFTER", "prepare_hold")
    paused = await first.run(RECOVERY, "alice", "party-thread", resume=True)
    assert paused["hold_intent"]["quantity"] == 3
    assert paused["hold_intent"]["total_amount"] == "300.00"
    monkeypatch.delenv("LANGGRAPH_DEMO_INTERRUPT_AFTER")
    second = workflow_factory()

    async def hold(state):
        assert state["travelers_count"] == 3
        assert state["hold_intent"]["quantity"] == 3
        return {"hold_id": state["hold_intent"]["booking_id"]}

    second._node_hold = hold
    await second.run("Resume workflow from checkpoint", "alice", "party-thread",
                     resume=True, travelers_count=1)


@pytest.mark.parametrize("quantity", [0, -1, 21, 1.5, True])
def test_chat_rejects_invalid_party_size(quantity):
    from pydantic import ValidationError
    from backend.routers.chat import ChatRequest
    with pytest.raises(ValidationError):
        ChatRequest(message="Tokyo", phase=5, travelers_count=quantity)


@pytest.mark.asyncio
async def test_workflow_failure_does_not_report_success_or_claim_no_changes(monkeypatch):
    from fastapi import HTTPException
    from backend.http_auth import HttpPrincipal
    from backend.routers import chat as router
    async def interrupted(*args, **kwargs):
        raise RuntimeError('connection interrupted after checkpoint')
    monkeypatch.setattr(router, 'orchestration_workflow', interrupted)
    with pytest.raises(HTTPException) as exc:
        await router.chat(router.ChatRequest(message='Tokyo recovery', phase=5), HttpPrincipal('test', 'alice', 'test'))
    assert exc.value.status_code == 503
    assert 'Re-read the saved journey' in exc.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize("named_error", ["execution_lease_lost", "journey_not_owned"])
async def test_lease_lost_at_the_lambda_stops_the_run_at_the_hold(
    workflow_factory, named_error
):
    def gateway(tool, arguments):
        return {"result": {"content": [{"type": "text",
                                        "text": json.dumps({"error": named_error})}]}}

    workflow = workflow_factory()
    workflow._prepare_governed_hold = AsyncMock(return_value=("journey", 200000))
    workflow._gateway_call = gateway
    thread = f"lease-lost-{named_error}"

    await workflow.run(RECOVERY, "alice", thread, travelers_count=2)
    with pytest.raises(module.ExecutionLeaseLostError):
        await workflow.run(RECOVERY, "alice", thread, resume=True)
    saved = await workflow.graph.aget_state({"configurable": {"thread_id": thread}})
    assert saved.next == ("hold",), "the stale worker must not checkpoint past the hold"


def _released_booking_boundary(monkeypatch, released_rows):
    """Stand in for Aurora and the AWS identity, the only external boundaries."""
    from contextlib import asynccontextmanager
    from unittest.mock import Mock

    import backend.agentcore.identity as identity
    import backend.db.rds_data_client as rds

    @asynccontextmanager
    async def scoped_session(**kwargs):
        yield "tx"

    db = Mock(scoped_session=scoped_session, execute=AsyncMock(return_value=released_rows))
    monkeypatch.setattr(rds, "get_rds_data_client", lambda: db)
    monkeypatch.setattr(identity, "get_agentcore_identity", lambda: Mock())
    return db


def _held_gateway(tool, arguments):
    hold = {"bookingId": arguments["bookingId"], "status": "held",
            "expiresAt": "2026-09-12T20:15:00Z", "createdAt": "2026-09-12T20:00:00Z",
            "observedAt": "2026-09-12T20:00:01Z"}
    return {"result": {"content": [{"type": "text", "text": json.dumps({"hold": hold})}]}}


@pytest.mark.asyncio
async def test_a_released_hold_is_recorded_in_the_checkpoint_and_the_resumed_reply(
    workflow_factory, monkeypatch
):
    _released_booking_boundary(monkeypatch, [{"booking_id": "released"}])
    thread = "compensated-thread"
    config = {"configurable": {"thread_id": thread}}
    query = "My flight was canceled. Rework my Tokyo trip and check availability."

    failing = workflow_factory()
    failing._prepare_governed_hold = AsyncMock(return_value=("journey", 200000))
    failing._gateway_call = _held_gateway
    failing._node_synthesize = AsyncMock(side_effect=RuntimeError("synthesis exploded"))
    await failing.run(query, "alice", thread, travelers_count=2)
    with pytest.raises(RuntimeError, match="synthesis exploded"):
        await failing.run(query, "alice", thread, resume=True)

    saved = await failing.graph.aget_state(config)
    assert saved.values["hold_status"] == "released"
    assert saved.next == ("synthesize",), "recording the release must not move the run"

    resumed = await workflow_factory().run("Resume workflow", "alice", thread, resume=True)
    assert resumed["hold_status"] == "released"
    assert "released" in resumed["response"].lower()
    assert "Expires at" not in resumed["response"]


@pytest.mark.asyncio
async def test_a_hold_that_was_not_released_keeps_its_checkpointed_status(
    workflow_factory, monkeypatch
):
    _released_booking_boundary(monkeypatch, [])
    thread = "uncompensated-thread"
    failing = workflow_factory()
    failing._prepare_governed_hold = AsyncMock(return_value=("journey", 200000))
    failing._gateway_call = _held_gateway
    failing._node_synthesize = AsyncMock(side_effect=RuntimeError("synthesis exploded"))
    await failing.run(RECOVERY, "alice", thread, travelers_count=2)
    with pytest.raises(RuntimeError, match="synthesis exploded"):
        await failing.run(RECOVERY, "alice", thread, resume=True)
    saved = await failing.graph.aget_state({"configurable": {"thread_id": thread}})
    assert saved.values["hold_status"] == "held"
