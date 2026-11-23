"""The Runtime's hold release note and its missing-token code survive the trip to the backend."""

import json
from unittest.mock import MagicMock

import pytest

from backend.agentcore import workflow_runtime as wr
from backend.agents.phase_05_workflow.runner import WorkflowCommand
from backend.db.journey_store import ExecutionLeaseLostError

ARN = "arn:aws:bedrock-agentcore:us-east-1:111122223333:runtime/meridianv2_MeridianWorkflow-x"
COMMAND = WorkflowCommand(query="My flight was canceled.", traveler_id="trv_meridian_demo",
                          thread_id="phase5-0123456789ab", resume=True, travelers_count=2)


def client_for(event):
    body = MagicMock()
    body.read.side_effect = [f"data: {json.dumps(json.dumps(event))}\n\n".encode(), b""]
    client = MagicMock()
    client.invoke_agent_runtime.return_value = {"response": body}
    return wr.WorkflowRuntimeClient(ARN, client=client)


async def test_an_error_event_carries_the_hold_release_note_on_the_exception():
    note = "hold left in place: another worker owns this run"
    runtime = client_for(
        {"type": "error", "code": "lease_lost", "message": "lost", "hold_release": note})
    with pytest.raises(ExecutionLeaseLostError) as caught:
        await runtime.run(COMMAND)
    assert caught.value.hold_release == note


async def test_the_hold_release_note_is_masked_and_cut():
    note = "hold not released: arn:aws:iam::123456789012:role/x " + "y" * 400
    runtime = client_for(
        {"type": "error", "code": "internal", "message": "m", "hold_release": note})
    with pytest.raises(RuntimeError) as caught:
        await runtime.run(COMMAND)
    assert "123456789012" not in caught.value.hold_release
    assert len(caught.value.hold_release) <= wr.HOLD_NOTE_LIMIT


async def test_an_error_event_without_a_note_carries_none():
    runtime = client_for({"type": "error", "code": "internal", "message": "m"})
    with pytest.raises(RuntimeError) as caught:
        await runtime.run(COMMAND)
    assert not hasattr(caught.value, "hold_release")


@pytest.mark.parametrize(("note", "words"), [
    ("hold left in place: another worker owns this run",
     "left in place because another worker owns this run"),
    ("hold not released: booking_not_held", "was not released (booking_not_held)"),
    ("hold release unconfirmed: the release call failed",
     "Whether the hold was released is not confirmed (the release call failed)"),
    ("something new", "something new"),
])
def test_a_hold_note_reads_as_a_plain_sentence(note, words):
    error = RuntimeError("x")
    error.hold_release = note
    sentence = wr.hold_release_sentence(error)
    assert words in sentence and sentence.endswith(".")


def test_no_note_means_no_sentence():
    assert wr.hold_release_sentence(RuntimeError("x")) == ""


async def test_a_missing_caller_token_at_the_runtime_is_its_own_server_error():
    runtime = client_for(
        {"type": "error", "code": "caller_token_missing", "message": "No caller token."})
    with pytest.raises(wr.WorkflowCallerTokenError):
        await runtime.run(COMMAND)
