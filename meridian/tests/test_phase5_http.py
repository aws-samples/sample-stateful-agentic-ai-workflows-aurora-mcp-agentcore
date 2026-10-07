"""Phase 5 over HTTP keeps its status contract on the workflow Runtime client."""

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from backend.agentcore.errors import AgentCoreNotConfiguredError
from backend.agents.phase_05_workflow.governed_hold import HoldOutcomeUnknown
from backend.agents.phase_05_workflow.nodes import WorkflowNodes
from backend.agents.phase_05_workflow.runner import (
    WorkflowConflictError,
    WorkflowRequestError,
    WorkflowRunner,
)
from backend.agents.phase_05_workflow.state import WorkflowAuthorizationError
from backend.db.journey_store import ExecutionLeaseLostError
from backend.http_auth import HttpPrincipal
from backend.routers import chat as router
from tests.phase5_support import InMemoryLease, fake_availability, fake_search

GET_RUNTIME = "backend.agentcore.workflow_runtime.get_workflow_runtime"


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
    (WorkflowRequestError("thread_id 'a/b' cannot be used"), 422),
])
async def test_runner_refusals_keep_their_status(monkeypatch, error, status):
    monkeypatch.setattr(GET_RUNTIME, lambda: Runner(error))
    with pytest.raises(HTTPException) as caught:
        await router.chat(router.ChatRequest(message="Tokyo recovery", phase=5), principal())
    assert caught.value.status_code == status


async def test_an_unconfigured_workflow_runtime_is_a_503_naming_the_setting(monkeypatch):
    error = AgentCoreNotConfiguredError(
        missing=("workflow_runtime_arn",), project_dir="p", sources=())
    monkeypatch.setattr(GET_RUNTIME, lambda: Runner(error))
    with pytest.raises(HTTPException) as caught:
        await router.chat(router.ChatRequest(message="Tokyo recovery", phase=5), principal())
    assert caught.value.status_code == 503
    assert "AGENTCORE_WORKFLOW_RUNTIME_ARN" in caught.value.detail


async def test_a_conversation_id_strands_cannot_use_is_a_422_before_any_claim(monkeypatch):
    lease = InMemoryLease()
    real_runner = WorkflowRunner(
        WorkflowNodes(fake_search, fake_availability),
        storage_for=lambda *args: pytest.fail("no storage may be built for a refused request"),
        lease=lease,
    )

    class InsideTheRuntime:
        async def run(self, command):
            return await real_runner.run(command)

    monkeypatch.setattr(GET_RUNTIME, lambda: InsideTheRuntime())
    with pytest.raises(HTTPException) as caught:
        await router.chat(
            router.ChatRequest(message="Tokyo recovery", phase=5, conversation_id="a/b"),
            principal(),
        )
    assert caught.value.status_code == 422
    assert "thread_id" in caught.value.detail
    assert lease.traveler_threads == {} and lease.executions == []


async def test_an_unknown_hold_outcome_says_to_reread(monkeypatch):
    unknown = HoldOutcomeUnknown("The hold outcome is unknown.")
    monkeypatch.setattr(GET_RUNTIME, lambda: Runner(unknown))
    with pytest.raises(HTTPException) as caught:
        await router.chat(
            router.ChatRequest(message="Resume workflow", phase=5, resume=True,
                               conversation_id="phase5-x"),
            principal(),
        )
    assert caught.value.status_code == 503
    assert "Re-read the saved journey" in caught.value.detail


async def test_any_other_failure_is_a_referenced_503(monkeypatch):
    monkeypatch.setattr(GET_RUNTIME, lambda: Runner(RuntimeError("boom")))
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
    monkeypatch.setattr(GET_RUNTIME, lambda: runner)
    response = await router.chat(
        router.ChatRequest(message="Rework my canceled Tokyo trip", phase=5), principal()
    )
    assert response.workflow_status == "paused"
    assert response.follow_ups == ["Resume workflow from the saved step"]
    assert runner.commands[0].thread_id.startswith("phase5-")
    assert runner.commands[0].resume is False


@pytest.mark.parametrize("quantity", [0, -1, 21, 1.5, True])
def test_chat_rejects_invalid_party_size(quantity):
    with pytest.raises(ValidationError):
        router.ChatRequest(message="Tokyo", phase=5, travelers_count=quantity)
