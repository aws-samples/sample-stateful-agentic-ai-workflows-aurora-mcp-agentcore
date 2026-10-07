"""Runtimes with a JWT authorizer are invoked over HTTPS with the caller's bearer token."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import httpx
import pytest

from backend.agentcore import runtime as runtime_module
from backend.agentcore.caller_credential import caller_token_scope
from backend.agentcore.errors import CallerTokenExpired, CallerTokenMissing
from backend.agentcore.runtime import AgentCoreRuntimeAdapter, iter_sse, stream_chunks
from backend.agentcore.runtime_https import (
    ConnectionDropped,
    RuntimeHttpClient,
    RuntimeHttpError,
    invocation_url,
)
from tests.jwt_support import access_token

ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/meridianv2_MeridianConcierge-x"
URL = invocation_url("us-east-1", ARN, "DEFAULT")


def sse(*events):
    return b"".join(f"data: {json.dumps(json.dumps(e))}\n\n".encode() for e in events)


def streamed(body: bytes) -> httpx.Response:
    """A 200 whose body arrives as a stream, like a real Runtime's."""
    return httpx.Response(200, content=iter([body]))


def client(handler):
    return RuntimeHttpClient(transport=httpx.MockTransport(handler))


def invoke(http, token=None, **kwargs):
    return http.invoke(url=URL, token=token or access_token(), session_id="rt-session-1",
                       payload=b'{"a":1}', **kwargs)


def test_the_url_encodes_the_arn_and_names_the_endpoint():
    assert URL == (
        "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/"
        "arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A123456789012%3Aruntime%2F"
        "meridianv2_MeridianConcierge-x/invocations?qualifier=DEFAULT"
    )


def test_the_request_carries_the_bearer_the_session_and_the_payload():
    seen = []

    def handler(request):
        seen.append(request)
        return streamed(sse({"type": "result", "message": "hi"}))

    token = access_token()
    invoke(client(handler), token)
    request = seen[0]
    assert request.method == "POST" and str(request.url) == URL
    assert request.headers["authorization"] == f"Bearer {token}"
    assert request.headers["x-amzn-bedrock-agentcore-runtime-session-id"] == "rt-session-1"
    assert request.headers["accept"] == "text/event-stream"
    assert request.headers["content-type"] == "application/json"
    assert request.content == b'{"a":1}'


def test_the_body_streams_in_the_shape_the_sse_reader_already_handles():
    events = [{"type": "token", "text": "Hel"}, {"type": "token", "text": "lo"},
              {"type": "result", "message": "Hello"}]
    frames = sse(*events)
    chunks = [frames[:7], frames[7:40], frames[40:]]
    response = invoke(client(lambda request: httpx.Response(200, content=iter(chunks))))
    assert list(iter_sse(stream_chunks(response))) == events


def test_read_returns_what_has_arrived_and_zero_bytes_only_at_the_end():
    response = invoke(client(lambda r: httpx.Response(200, content=iter([b"abcdef", b"gh"]))))
    body = response["response"]
    assert [body.read(4), body.read(4), body.read(4), body.read(4)] == [b"abcd", b"ef", b"gh", b""]
    body.close()


@pytest.mark.parametrize(("status", "code"), [
    (409, "RetryableConflictException"), (429, "ThrottlingException"),
    (404, "ResourceNotFoundException"), (500, "InternalServerException"),
    (401, "UnauthorizedException"), (403, "AccessDeniedException"), (418, "HTTP418"),
])
def test_an_error_status_carries_the_code_boto3_would_have_raised(status, code):
    with pytest.raises(RuntimeHttpError) as raised:
        invoke(client(lambda request: httpx.Response(status, json={"message": "no"})))
    assert (raised.value.code, raised.value.status) == (code, status)
    assert str(raised.value) == f"AgentCore Runtime invoke failed: {code}"


def test_a_refusal_after_the_token_ran_out_in_flight_is_a_coded_expiry(monkeypatch):
    checks = iter([None, CallerTokenExpired("expired in flight")])

    def ensure_unexpired(token):
        outcome = next(checks)
        if outcome:
            raise outcome

    monkeypatch.setattr("backend.agentcore.runtime_https.ensure_unexpired", ensure_unexpired)
    with pytest.raises(CallerTokenExpired):
        invoke(client(lambda request: httpx.Response(401, json={"message": "expired"})))


def test_an_expired_token_is_never_sent():
    sent = []
    with pytest.raises(CallerTokenExpired):
        invoke(client(lambda request: sent.append(request)), access_token(expires_in=-5))
    assert sent == []


def test_a_connection_closed_before_headers_is_retryable():
    def handler(request):
        raise httpx.RemoteProtocolError("Server disconnected without sending a response.")

    with pytest.raises(ConnectionDropped):
        invoke(client(handler))


def test_other_transport_failures_are_plain_errors_that_never_contain_the_token():
    token = access_token()

    def handler(request):
        raise httpx.ConnectTimeout(f"timed out calling with {token}")

    with pytest.raises(RuntimeHttpError) as raised:
        invoke(client(handler), token)
    assert raised.value.code == "ConnectionError" and token not in str(raised.value)


# ---- the Concierge adapter chooses the path from the mode ---------------------------------------

RESULT = sse({"type": "result", "message": "Here are two trips.", "recommended_package_ids": []})


def adapter(handler=None, boto=None):
    http = client(handler) if handler else None
    built = AgentCoreRuntimeAdapter(runtime_arn=ARN, qualifier="DEFAULT", region="us-east-1",
                                    http=http)
    built._client = boto
    return built


def turn(built, **kwargs):
    return built.invoke_turn("conv-1", "trv_meridian_demo", "hello", "", budget_ceiling_cents=1,
                             travelers_count=1, **kwargs)


def test_iam_mode_uses_boto_and_never_the_https_client(monkeypatch):
    monkeypatch.delenv("MERIDIAN_AGENTCORE_AUTH", raising=False)
    body = MagicMock()
    body.read.side_effect = [RESULT, b""]
    boto = MagicMock()
    boto.invoke_agent_runtime.return_value = {"response": body}
    https_calls = []
    decision = turn(adapter(lambda request: https_calls.append(request), boto))
    assert decision.message == "Here are two trips." and https_calls == []
    assert boto.invoke_agent_runtime.call_args.kwargs["accept"] == "text/event-stream"


def test_jwt_mode_posts_with_the_callers_token_and_never_calls_boto(monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    seen, boto, token = [], MagicMock(), access_token()

    def handler(request):
        seen.append(request)
        return streamed(RESULT)

    with caller_token_scope(token):
        decision = turn(adapter(handler, boto))
    assert decision.message == "Here are two trips."
    assert seen[0].headers["authorization"] == f"Bearer {token}"
    assert json.loads(seen[0].content)["event"] == "concierge_turn"
    boto.invoke_agent_runtime.assert_not_called()


def test_jwt_mode_without_a_bound_token_fails_before_the_network(monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    seen = []
    with pytest.raises(CallerTokenMissing):
        turn(adapter(lambda request: seen.append(request)))
    assert seen == []


def test_an_unconfirmed_turn_retries_once_after_a_dropped_connection(monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    monkeypatch.setattr(runtime_module.time, "sleep", lambda s: None)
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.RemoteProtocolError("closed")
        return streamed(RESULT)

    with caller_token_scope(access_token()):
        assert turn(adapter(handler)).message == "Here are two trips."
    assert len(attempts) == 2


def test_a_confirmed_hold_is_never_retried(monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    attempts = []

    def handler(request):
        attempts.append(1)
        raise httpx.RemoteProtocolError("closed")

    target = {"package_id": "CTY-002", "duration": "7 nights", "travelers": 2,
              "unit_price_cents": 1000}
    with caller_token_scope(access_token()), pytest.raises(ConnectionDropped):
        turn(adapter(handler), hold_confirmed=True, hold_target=target)
    assert len(attempts) == 1


def test_an_error_status_surfaces_as_a_runtime_error_with_its_code(monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    with caller_token_scope(access_token()), pytest.raises(RuntimeHttpError) as raised:
        turn(adapter(lambda request: httpx.Response(429, json={})))
    assert raised.value.code == "ThrottlingException"


def test_a_token_expired_event_from_the_runtime_becomes_the_coded_expiry(monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    body = sse({"type": "error", "code": "token_expired", "message": "Sign in again."})
    with caller_token_scope(access_token()), pytest.raises(CallerTokenExpired):
        turn(adapter(lambda request: streamed(body)))


def test_another_error_event_stays_a_runtime_error(monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    body = sse({"type": "error", "code": "authorization", "message": "different traveler"})
    with caller_token_scope(access_token()), pytest.raises(RuntimeError, match="different"):
        turn(adapter(lambda request: streamed(body)))
