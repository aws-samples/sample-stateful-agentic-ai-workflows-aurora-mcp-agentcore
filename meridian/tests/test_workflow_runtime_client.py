"""The backend's client for MeridianWorkflow: typed payload, coded errors, safe retries."""

import json
import logging
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

from backend.agentcore import workflow_runtime as wr
from backend.agentcore.runtime import AgentCoreRuntimeAdapter
from backend.agents.phase_05_workflow.governed_hold import HoldOutcomeUnknown
from backend.agents.phase_05_workflow.runner import (
    WorkflowCommand, WorkflowConflictError, WorkflowRequestError,
)
from backend.agents.phase_05_workflow.state import WorkflowAuthorizationError
from backend.db.journey_store import ExecutionLeaseLostError

ARN = "arn:aws:bedrock-agentcore:us-east-1:111122223333:runtime/meridianv2_MeridianWorkflow-x"
COMMAND = WorkflowCommand(query="My flight was canceled.", traveler_id="trv_meridian_demo",
                          thread_id="phase5-0123456789ab", resume=True, travelers_count=2)


def frames(*events):
    body = MagicMock()
    body.read.side_effect = [f"data: {json.dumps(json.dumps(e))}\n\n".encode() for e in events] + [b""]
    return {"response": body}


def client_with(*responses):
    client = MagicMock()
    client.invoke_agent_runtime.side_effect = list(responses)
    return client


def conflict(code="RetryableConflictException"):
    return ClientError({"Error": {"Code": code, "Message": "Session operation in progress"}},
                       "InvokeAgentRuntime")


async def test_the_payload_carries_only_the_typed_fields():
    client = client_with(frames({"type": "heartbeat"}, {"type": "result", "state": {"workflow_status": "resumed"}}))
    state = await wr.WorkflowRuntimeClient(ARN, client=client).run(COMMAND)
    kwargs = client.invoke_agent_runtime.call_args.kwargs
    assert json.loads(kwargs["payload"]) == {
        "event": "workflow_turn", "mode": "resume", "thread_id": "phase5-0123456789ab",
        "traveler_id": "trv_meridian_demo", "query": "My flight was canceled.",
        "travelers_count": 2, "review_only": False,
    }
    assert kwargs["runtimeSessionId"] == wr.workflow_session_id("trv_meridian_demo", "phase5-0123456789ab")
    assert kwargs["accept"] == "text/event-stream"
    assert state == {"workflow_status": "resumed"}


def test_session_ids_are_stable_long_enough_and_distinct_from_the_concierge():
    first = wr.workflow_session_id("trv_meridian_demo", "phase5-0123456789ab")
    assert first == wr.workflow_session_id("trv_meridian_demo", "phase5-0123456789ab")
    assert len(first) >= 33 and first.startswith("rt-wf-phase5-0123456789ab-")
    assert first != AgentCoreRuntimeAdapter._build_runtime_session_id("phase5-0123456789ab", "trv_meridian_demo")
    assert first != wr.workflow_session_id("trv_demo_decoy", "phase5-0123456789ab")


@pytest.mark.parametrize(("code", "error"), [
    ("request", WorkflowRequestError), ("authorization", WorkflowAuthorizationError),
    ("conflict", WorkflowConflictError), ("lease_lost", ExecutionLeaseLostError),
    ("hold_unknown", HoldOutcomeUnknown), ("internal", RuntimeError),
])
async def test_error_codes_become_the_domain_errors(code, error):
    client = client_with(frames({"type": "error", "code": code, "message": "m"}))
    with pytest.raises(error, match="m"):
        await wr.WorkflowRuntimeClient(ARN, client=client).run(COMMAND)


async def test_a_session_being_provisioned_is_retried_then_runs():
    client = client_with(conflict(), conflict(), frames({"type": "result", "state": {}}))
    sleeps = []
    await wr.WorkflowRuntimeClient(ARN, client=client, sleep=sleeps.append).run(COMMAND)
    assert client.invoke_agent_runtime.call_count == 3 and sleeps == [0.5, 1.0]


async def test_other_invoke_failures_are_not_retried():
    client = client_with(conflict("ThrottlingException"))
    with pytest.raises(RuntimeError, match="ThrottlingException"):
        await wr.WorkflowRuntimeClient(ARN, client=client).run(COMMAND)
    assert client.invoke_agent_runtime.call_count == 1


async def test_five_conflicts_in_a_row_fail_after_five_calls():
    client = client_with(*[conflict() for _ in range(5)])
    sleeps = []
    with pytest.raises(RuntimeError, match="RetryableConflictException"):
        await wr.WorkflowRuntimeClient(ARN, client=client, sleep=sleeps.append).run(COMMAND)
    assert client.invoke_agent_runtime.call_count == 5 and sleeps == [0.5, 1.0, 2.0, 4.0]


async def test_a_mid_stream_read_timeout_propagates_without_a_second_invoke():
    body = MagicMock()
    body.read.side_effect = ReadTimeoutError(endpoint_url="https://example.invalid")
    client = client_with({"response": body})
    with pytest.raises(ReadTimeoutError):
        await wr.WorkflowRuntimeClient(ARN, client=client).run(COMMAND)
    assert client.invoke_agent_runtime.call_count == 1
    body.close.assert_called_once()


async def test_the_body_is_closed_after_a_result():
    response = frames({"type": "result", "state": {}}, {"type": "heartbeat"})
    await wr.WorkflowRuntimeClient(ARN, client=client_with(response)).run(COMMAND)
    response["response"].close.assert_called_once()


async def test_the_body_is_closed_after_an_error():
    response = frames({"type": "error", "code": "conflict", "message": "m"})
    with pytest.raises(WorkflowConflictError):
        await wr.WorkflowRuntimeClient(ARN, client=client_with(response)).run(COMMAND)
    response["response"].close.assert_called_once()


async def test_a_failed_invoke_logs_the_code_and_session_but_not_the_payload(caplog):
    client = client_with(conflict("ThrottlingException"))
    with caplog.at_level(logging.WARNING, logger=wr.logger.name):
        with pytest.raises(RuntimeError):
            await wr.WorkflowRuntimeClient(ARN, client=client).run(COMMAND)
    session = wr.workflow_session_id("trv_meridian_demo", "phase5-0123456789ab")
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "ThrottlingException" in text and session in text
    assert "My flight was canceled." not in text


async def test_a_failed_stop_logs_the_code_and_session(caplog):
    client = MagicMock()
    client.stop_runtime_session.side_effect = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "slow"}}, "StopRuntimeSession")
    with caplog.at_level(logging.WARNING, logger=wr.logger.name):
        with pytest.raises(RuntimeError, match="ThrottlingException"):
            await wr.WorkflowRuntimeClient(ARN, client=client).stop_session("t", "phase5-x")
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "ThrottlingException" in text and wr.workflow_session_id("t", "phase5-x") in text


async def test_a_stream_without_a_result_is_an_error():
    client = client_with(frames({"type": "heartbeat"}))
    with pytest.raises(RuntimeError, match="Re-read the saved journey"):
        await wr.WorkflowRuntimeClient(ARN, client=client).run(COMMAND)


async def test_stop_targets_the_derived_session():
    client = MagicMock()
    stop = await wr.WorkflowRuntimeClient(ARN, client=client).stop_session(
        "trv_meridian_demo", "phase5-0123456789ab")
    kwargs = client.stop_runtime_session.call_args.kwargs
    assert kwargs["runtimeSessionId"] == stop.runtime_session_id == wr.workflow_session_id(
        "trv_meridian_demo", "phase5-0123456789ab")
    assert kwargs["agentRuntimeArn"] == ARN and stop.outcome == "stopped"


async def test_stopping_a_session_that_already_ended_reports_not_running():
    client = MagicMock()
    client.stop_runtime_session.side_effect = ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "no session"}}, "StopRuntimeSession")
    stop = await wr.WorkflowRuntimeClient(ARN, client=client).stop_session("t", "phase5-x")
    assert stop.outcome == "not_running"


def test_the_sdk_client_never_retries_and_reads_with_a_45_second_gap(monkeypatch):
    made = {}
    monkeypatch.setattr(wr.boto3, "client", lambda service, **kw: made.update(service=service, **kw) or MagicMock())
    wr.WorkflowRuntimeClient(ARN)._client_for()
    config = made["config"]
    assert made["service"] == "bedrock-agentcore"
    assert (config.retries["total_max_attempts"], config.read_timeout, config.connect_timeout) == (1, 45, 5)


def test_a_missing_arn_names_the_setting(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTCORE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setenv("AGENTCORE_SKIP_CLI_SYNC", "1")
    monkeypatch.delenv("AGENTCORE_WORKFLOW_RUNTIME_ARN", raising=False)
    from backend.agentcore.cli_config import resolve_agentcore_config
    resolve_agentcore_config.cache_clear()
    with pytest.raises(Exception, match="workflow_runtime_arn"):
        wr.WorkflowRuntimeClient()._arn()
