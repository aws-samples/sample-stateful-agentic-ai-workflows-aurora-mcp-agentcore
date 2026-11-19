"""The documented Phase 5 paths: Start recovery, a fresh free-text read, and a recognized resume."""

import asyncio
from unittest.mock import AsyncMock

from backend.http_auth import HttpPrincipal
from backend.prompt_ladder import PROMPT_LADDER
from backend.routers.chat import ChatRequest, chat
from tests.test_workflow_runner import World, command

PRINCIPAL = HttpPrincipal(
    subject_id="test-client", traveler_id="trv_meridian_demo", authentication="test"
)
CANONICAL = PROMPT_LADDER[5].works[0]
PAUSED = ([], [], "Paused.", "recovery-thread", "paused", False, None)


def _chat(monkeypatch, request, result=PAUSED):
    workflow = AsyncMock(return_value=result)
    monkeypatch.setattr("backend.routers.chat.run_workflow_turn", workflow)
    asyncio.run(chat(request, PRINCIPAL))
    return workflow


def test_start_recovery_is_one_workflow_turn_carrying_the_prompt_and_the_intent(monkeypatch):
    workflow = _chat(monkeypatch, ChatRequest(
        message=CANONICAL, phase=5, conversation_id="recovery-thread", intent="recovery"))
    workflow.assert_awaited_once()
    assert workflow.call_args.args[0] == CANONICAL
    assert workflow.call_args.kwargs["recovery"] is True
    assert workflow.call_args.kwargs["resume"] is False


def test_the_same_prompt_in_a_fresh_conversation_declares_no_recovery(monkeypatch):
    workflow = _chat(monkeypatch, ChatRequest(message=CANONICAL, phase=5))
    assert workflow.call_args.kwargs["resume"] is False
    assert "recovery" not in workflow.call_args.kwargs


def test_a_recognized_resume_command_is_a_resume_without_a_recovery_declaration(monkeypatch):
    workflow = _chat(monkeypatch, ChatRequest(
        message="Resume workflow from the saved step", phase=5,
        conversation_id="recovery-thread"))
    assert workflow.call_args.kwargs["resume"] is True
    assert "recovery" not in workflow.call_args.kwargs


async def test_the_canonical_prompt_without_the_intent_reads_and_never_pauses_or_holds():
    world = World()
    result = await world.runner().run(command(CANONICAL, recovery=False))
    assert result["workflow_status"] != "paused"
    assert world.gateway.calls == []
    assert [e["status"] for e in world.lease.executions] == ["succeeded"]


async def test_the_canonical_prompt_with_the_intent_pauses_for_review_before_any_hold():
    world = World()
    result = await world.runner().run(command(CANONICAL, recovery=True))
    assert result["workflow_status"] == "paused"
    assert world.gateway.calls == []


async def test_a_recognized_resume_on_a_paused_recovery_authorizes_the_pending_step():
    world = World()
    await world.runner().run(command(CANONICAL, recovery=True))
    assert world.gateway.calls == []

    result = await world.runner("worker-b").run(command("Resume workflow", resume=True))

    assert result["workflow_status"] == "resumed"
    [call] = world.gateway.calls
    assert call["travelerConfirmed"] is True
    assert result["hold_id"] == call["bookingId"]
    assert [e["status"] for e in world.lease.executions] == ["paused", "succeeded"]
