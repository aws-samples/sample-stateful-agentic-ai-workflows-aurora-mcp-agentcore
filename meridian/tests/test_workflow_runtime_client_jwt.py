"""In jwt mode the workflow client posts with the caller's token, retries and codes unchanged."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import pytest

from backend.agentcore.caller_credential import caller_token_scope
from backend.agentcore.errors import CallerTokenExpired, CallerTokenMissing
from backend.agentcore.runtime_https import RuntimeHttpClient
from backend.agentcore import workflow_runtime as wr
from backend.agents.phase_05_workflow.runner import WorkflowCommand
from tests.jwt_support import access_token

ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/meridianv2_MeridianWorkflow-x"
COMMAND = WorkflowCommand(query="My flight was canceled.", traveler_id="trv_meridian_demo",
                          thread_id="phase5-0123456789ab", resume=True, travelers_count=2)


def sse(*events):
    return b"".join(f"data: {json.dumps(json.dumps(e))}\n\n".encode() for e in events)


def streamed(*events):
    return httpx.Response(200, content=iter([sse(*events)]))


def workflow_client(handler, boto=None, sleeps=None):
    return wr.WorkflowRuntimeClient(
        ARN, region="us-east-1", client=boto,
        http=RuntimeHttpClient(transport=httpx.MockTransport(handler)),
        sleep=(sleeps.append if sleeps is not None else (lambda s: None)))


@pytest.fixture(autouse=True)
def jwt(monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")


async def test_a_run_posts_the_typed_payload_with_the_callers_token():
    seen, token, boto = [], access_token(), MagicMock()

    def handler(request):
        seen.append(request)
        return streamed({"type": "heartbeat"},
                        {"type": "result", "state": {"workflow_status": "resumed"}})

    with caller_token_scope(token):
        state = await workflow_client(handler, boto).run(COMMAND)
    request = seen[0]
    assert state == {"workflow_status": "resumed"}
    assert request.headers["authorization"] == f"Bearer {token}"
    assert request.headers["x-amzn-bedrock-agentcore-runtime-session-id"] == (
        wr.workflow_session_id("trv_meridian_demo", "phase5-0123456789ab"))
    assert json.loads(request.content) == json.loads(wr.WorkflowRuntimeClient.payload(COMMAND))
    boto.invoke_agent_runtime.assert_not_called()


async def test_without_a_bound_token_nothing_is_sent():
    seen = []
    with pytest.raises(CallerTokenMissing):
        await workflow_client(lambda request: seen.append(request)).run(COMMAND)
    assert seen == []


async def test_a_session_being_provisioned_is_retried_with_the_same_delays():
    replies = iter([httpx.Response(409, json={}), httpx.Response(409, json={}),
                    streamed({"type": "result", "state": {}})])
    sleeps = []
    with caller_token_scope(access_token()):
        await workflow_client(lambda request: next(replies), sleeps=sleeps).run(COMMAND)
    assert sleeps == [0.5, 1.0]


async def test_any_other_status_is_a_plain_error_with_its_code():
    with caller_token_scope(access_token()), pytest.raises(RuntimeError) as raised:
        await workflow_client(lambda request: httpx.Response(429, json={})).run(COMMAND)
    assert str(raised.value) == "Workflow Runtime invoke failed: ThrottlingException"


async def test_a_token_expired_error_event_becomes_the_coded_expiry():
    body = streamed({"type": "error", "code": "token_expired", "message": "Sign in again."})
    with caller_token_scope(access_token()), pytest.raises(CallerTokenExpired, match="Sign in"):
        await workflow_client(lambda request: body).run(COMMAND)


async def test_other_error_codes_keep_their_domain_errors():
    from backend.agents.phase_05_workflow.state import WorkflowAuthorizationError

    body = streamed({"type": "error", "code": "authorization", "message": "different traveler"})
    with caller_token_scope(access_token()), pytest.raises(WorkflowAuthorizationError):
        await workflow_client(lambda request: body).run(COMMAND)


async def test_a_ping_uses_the_token_too():
    seen = []

    def handler(request):
        seen.append(request)
        return streamed({"type": "result", "state": {"workflow_status": "ready"}})

    with caller_token_scope(access_token()):
        state = await workflow_client(handler).ping("rt-wf-smoke-" + "0" * 32)
    assert state["workflow_status"] == "ready" and json.loads(seen[0].content)["mode"] == "ping"


async def test_stopping_a_session_is_still_iam_signed():
    boto = MagicMock()
    stop = await workflow_client(lambda request: pytest.fail("no HTTPS call"), boto).stop_session(
        "trv_meridian_demo", "phase5-0123456789ab")
    assert stop.outcome == "stopped"
    assert boto.stop_runtime_session.call_args.kwargs["agentRuntimeArn"] == ARN


async def test_an_expired_token_is_refused_before_anything_is_sent():
    seen = []
    with caller_token_scope(access_token(expires_in=-60)), pytest.raises(CallerTokenExpired):
        await workflow_client(lambda request: seen.append(request)).run(COMMAND)
    assert seen == []


async def test_expiry_leaves_the_journey_alone_and_a_fresh_token_resumes():
    boto, calls = MagicMock(), []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return streamed({"type": "error", "code": "token_expired", "message": "Sign in."})
        return streamed({"type": "result", "state": {"workflow_status": "resumed"}})

    client = workflow_client(handler, boto)
    with caller_token_scope(access_token()), pytest.raises(CallerTokenExpired):
        await client.run(COMMAND)
    fresh = access_token()
    with caller_token_scope(fresh):
        state = await client.run(COMMAND)
    assert state == {"workflow_status": "resumed"}
    assert calls[1].headers["authorization"] == f"Bearer {fresh}"
    assert calls[0].headers["x-amzn-bedrock-agentcore-runtime-session-id"] == (
        calls[1].headers["x-amzn-bedrock-agentcore-runtime-session-id"])
    assert boto.mock_calls == []


async def test_a_missing_token_error_event_is_an_authorization_error():
    from backend.agents.phase_05_workflow.state import WorkflowAuthorizationError

    assert wr.ERRORS["authorization"] is WorkflowAuthorizationError
    assert wr.ERRORS["token_expired"] is CallerTokenExpired


async def test_iam_mode_still_signs_with_boto_and_never_uses_https(monkeypatch):
    monkeypatch.delenv("MERIDIAN_AGENTCORE_AUTH")
    body = MagicMock()
    body.read.side_effect = [sse({"type": "result", "state": {"workflow_status": "ok"}}), b""]
    boto = MagicMock()
    boto.invoke_agent_runtime.return_value = {"response": body}
    client = workflow_client(lambda request: pytest.fail("no HTTPS call"), boto)
    assert await client.run(COMMAND) == {"workflow_status": "ok"}
    kwargs = boto.invoke_agent_runtime.call_args.kwargs
    assert kwargs["agentRuntimeArn"] == ARN and kwargs["qualifier"] == "DEFAULT"
    assert kwargs["runtimeSessionId"] == wr.workflow_session_id(
        "trv_meridian_demo", "phase5-0123456789ab")


async def test_stopping_a_session_is_iam_signed_in_iam_mode_too(monkeypatch):
    monkeypatch.delenv("MERIDIAN_AGENTCORE_AUTH")
    boto = MagicMock()
    stop = await workflow_client(lambda request: pytest.fail("no HTTPS call"), boto).stop_session(
        "trv_meridian_demo", "phase5-0123456789ab")
    assert stop.outcome == "stopped"
    boto.stop_runtime_session.assert_called_once()

