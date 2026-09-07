"""Real graph regressions with an isolated saver and no live business actions."""
import pytest
from langgraph.checkpoint.memory import MemorySaver

import backend.agents.orchestration_05.workflow as module
from backend.agents.orchestration_05.workflow import (
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
        result = await workflow.run(
            f"My flight was canceled. Rework my {destination} trip and check availability.",
            "alice", "recovery-thread",
        )
        assert result["hold_intent"]["package_id"] == destination
    assert [intent["package_id"] for intent in held] == ["Tokyo", "Paris", "Tokyo"]
    assert len({intent["hold_request_id"] for intent in held}) == 3
    assert len({intent["booking_id"] for intent in held}) == 3


@pytest.mark.asyncio
async def test_resume_preserves_the_checkpointed_business_intent(workflow_factory, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_DEMO_INTERRUPT_AFTER", "prepare_hold")
    first = workflow_factory()
    paused = await first.run(
        "My flight was canceled. Rework my Tokyo trip and check availability.",
        "alice", "paused-thread",
    )
    assert paused["workflow_status"] == "paused"
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
async def test_resume_uses_checkpointed_party_size(workflow_factory, monkeypatch):
    monkeypatch.setenv("LANGGRAPH_DEMO_INTERRUPT_AFTER", "prepare_hold")
    first = workflow_factory()
    paused = await first.run(
        "My flight was canceled. Rework my Tokyo trip and check availability.",
        "alice", "party-thread", travelers_count=3,
    )
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
async def test_document_restores_pending_nodes_and_completed_resume(workflow_factory, monkeypatch):
    from backend.db.journey_document import _workflow_document
    monkeypatch.setenv("LANGGRAPH_DEMO_INTERRUPT_AFTER", "search")
    workflow = workflow_factory()
    await workflow.run("Plan Tokyo and check availability", "alice", "refresh-thread", travelers_count=3)
    config = {"configurable": {"thread_id": "refresh-thread"}}
    snapshot = await workflow.graph.aget_state(config)
    checkpoint = {"thread_id": "refresh-thread", "checkpoint_id": snapshot.config["configurable"]["checkpoint_id"]}
    doc = _workflow_document(snapshot, checkpoint)
    assert doc["workflow_status"] == "paused"
    assert doc["next_nodes"] == ["availability"]
    assert doc["travelers_count"] == 3
    monkeypatch.delenv("LANGGRAPH_DEMO_INTERRUPT_AFTER")
    await workflow_factory().run("Resume workflow from checkpoint", "alice", "refresh-thread", resume=True)
    reader = workflow_factory()
    await reader._ensure_checkpoint_backend()
    snapshot = await reader.graph.aget_state(config)
    doc = _workflow_document(snapshot, checkpoint)
    assert doc["workflow_status"] == "resumed"
    assert doc["resumed_from_checkpoint"] == checkpoint["checkpoint_id"]
    assert doc["next_nodes"] == []


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
