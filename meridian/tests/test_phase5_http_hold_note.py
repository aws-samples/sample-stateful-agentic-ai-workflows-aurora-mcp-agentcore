"""A failed Phase 5 run tells the traveler what happened to the hold; a missing token is a 503."""

import pytest
from fastapi import HTTPException

from backend.agentcore.workflow_runtime import WorkflowCallerTokenError
from backend.agents.phase_05_workflow.governed_hold import HoldOutcomeUnknown
from backend.db.journey_store import ExecutionLeaseLostError
from backend.http_auth import HttpPrincipal
from backend.routers import chat as router

GET_RUNTIME = "backend.agentcore.workflow_runtime.get_workflow_runtime"


class Runner:
    def __init__(self, error):
        self.error = error

    async def run(self, command):
        raise self.error


def noted(error, note):
    error.hold_release = note
    return error


async def fail_with(monkeypatch, error):
    monkeypatch.setattr(GET_RUNTIME, lambda: Runner(error))
    with pytest.raises(HTTPException) as caught:
        await router.chat(router.ChatRequest(message="Tokyo recovery", phase=5),
                          HttpPrincipal("test", "trv_meridian_demo", "test"))
    return caught.value


async def test_a_failed_run_says_the_hold_was_not_released(monkeypatch):
    error = noted(RuntimeError("boom"), "hold not released: booking_not_held")
    failure = await fail_with(monkeypatch, error)
    assert failure.status_code == 503
    assert "Recovery was interrupted" in failure.detail and "Reference" in failure.detail
    assert "The hold on this trip was not released (booking_not_held)" in failure.detail
    assert "boom" not in failure.detail


async def test_an_unknown_hold_outcome_keeps_its_note(monkeypatch):
    error = noted(HoldOutcomeUnknown("The hold outcome is unknown."),
                  "hold release unconfirmed: the release call failed")
    failure = await fail_with(monkeypatch, error)
    assert failure.status_code == 503
    assert "Re-read the saved journey" in failure.detail
    assert "Whether the hold was released is not confirmed" in failure.detail


async def test_a_lost_lease_409_says_the_hold_was_left_in_place(monkeypatch):
    error = noted(ExecutionLeaseLostError("lost"),
                  "hold left in place: another worker owns this run")
    failure = await fail_with(monkeypatch, error)
    assert failure.status_code == 409
    assert "left in place because another worker owns this run" in failure.detail


async def test_a_failure_with_no_note_keeps_the_old_detail(monkeypatch):
    failure = await fail_with(monkeypatch, RuntimeError("boom"))
    assert "hold" not in failure.detail.lower()


async def test_a_token_missing_at_the_runtime_is_a_server_fault_not_a_sign_out(monkeypatch):
    failure = await fail_with(monkeypatch, WorkflowCallerTokenError("No caller token."))
    assert failure.status_code == 503
    assert "did not pass your sign-in on" in failure.detail
    assert "sign in again" not in failure.detail.lower()
