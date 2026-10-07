"""Phase 5 over HTTP keeps its contract on the Strands runner."""

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.agents.phase_05_workflow.governed_hold import HoldOutcomeUnknown
from backend.agents.phase_05_workflow.runner import WorkflowConflictError
from backend.agents.phase_05_workflow.state import WorkflowAuthorizationError
from backend.db.journey_store import ExecutionLeaseLostError
from backend.http_auth import HttpPrincipal
from backend.routers import chat as router

BUILD = "backend.agents.phase_05_workflow.service.build_workflow_runner"


class Runner:
    def __init__(self, outcome):
        self.outcome = outcome
        self.commands = []

    async def run(self, command):
        self.commands.append(command)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def principal():
    return HttpPrincipal("test", "trv_meridian_demo", "test")


@pytest.mark.parametrize("error, status", [
    (WorkflowAuthorizationError("Thread t belongs to another traveler."), 403),
    (WorkflowConflictError("This recovery is already running."), 409),
    (ExecutionLeaseLostError("lost"), 409),
])
async def test_runner_refusals_keep_their_status(monkeypatch, error, status):
    monkeypatch.setattr(BUILD, lambda: Runner(error))
    with pytest.raises(HTTPException) as caught:
        await router.chat(router.ChatRequest(message="Tokyo recovery", phase=5), principal())
    assert caught.value.status_code == status


async def test_an_unknown_hold_outcome_says_to_reread(monkeypatch):
    monkeypatch.setattr(BUILD, lambda: Runner(HoldOutcomeUnknown("The hold outcome is unknown.")))
    with pytest.raises(HTTPException) as caught:
        await router.chat(
            router.ChatRequest(message="Resume workflow", phase=5, resume=True,
                               conversation_id="phase5-x"),
            principal(),
        )
    assert caught.value.status_code == 503
    assert "Re-read the saved journey" in caught.value.detail


async def test_any_other_failure_is_a_referenced_503(monkeypatch):
    monkeypatch.setattr(BUILD, lambda: Runner(RuntimeError("boom")))
    with pytest.raises(HTTPException) as caught:
        await router.chat(router.ChatRequest(message="Tokyo recovery", phase=5), principal())
    assert caught.value.status_code == 503
    assert "Recovery was interrupted" in caught.value.detail
    assert "boom" not in caught.value.detail


async def test_a_paused_run_returns_the_resume_chip_and_a_generated_thread(monkeypatch):
    runner = Runner({
        "workflow_status": "paused",
        "response": "Workflow paused after a committed checkpoint.",
        "activities": [], "packages": [], "conversation_id": "phase5-generated",
    })
    monkeypatch.setattr(BUILD, lambda: runner)
    response = await router.chat(
        router.ChatRequest(message="Rework my canceled Tokyo trip", phase=5), principal()
    )
    assert response.workflow_status == "paused"
    assert response.follow_ups == ["Resume workflow from checkpoint"]
    assert runner.commands[0].thread_id.startswith("phase5-")
    assert runner.commands[0].resume is False


@pytest.mark.parametrize("quantity", [0, -1, 21, 1.5, True])
def test_chat_rejects_invalid_party_size(quantity):
    with pytest.raises(ValidationError):
        router.ChatRequest(message="Tokyo", phase=5, travelers_count=quantity)
