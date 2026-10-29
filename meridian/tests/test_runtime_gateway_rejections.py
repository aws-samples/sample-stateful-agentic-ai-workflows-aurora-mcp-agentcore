"""A Gateway 401 or 403 ends a Phase 4 turn as a setup failure; nothing else on the call path does.

The MCP client reports an HTTP rejection as "Connection to the MCP server was closed", the text a
dropped connection gets too. The Runtime therefore records the Gateway's HTTP status through an
``httpx.Auth`` wrapper and the stop hook decides on that record. These tests run the real Strands
``MCPClient`` and the Runtime's ``run`` against an in-process Gateway (``httpx.MockTransport``): no
server, no network.

Hook order: the stop hook is registered after the trace hooks. ``AfterToolCallEvent`` callbacks
run in reverse, so the stop hook runs first and sees the result before the trace hooks rewrite it.
"""

from __future__ import annotations

import asyncio
import copy
import json
import logging
import sys
import threading
import time
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import boto3
import httpx
import pytest
from mcp.client.streamable_http import streamable_http_client
from strands.session import FileSessionManager
from strands.tools.mcp import MCPClient

from tests.runtime_support import load_runtime_main
from tests.test_strands_hooks import ScriptedModel, calls, calls_each, says

RUNTIME = Path(__file__).resolve().parents[1] / "meridian_agentcore" / "app" / "MeridianConcierge"
sys.path.insert(0, str(RUNTIME))

import gateway_auth  # noqa: E402
import tool_errors  # noqa: E402
import turn_watchdog  # noqa: E402
import turn_work  # noqa: E402

try:
    # bedrock-agentcore 1.24.0 declares a class-based pydantic ``Config`` in its own config
    # module, which warns once on import. The warning is the SDK's, not this suite's.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from bedrock_agentcore.memory.integrations.strands.config import (  # noqa: E402
            AgentCoreMemoryConfig as RealMemoryConfig,
        )
        from bedrock_agentcore.memory.integrations.strands.config import (  # noqa: E402
            RetrievalConfig as RealRetrievalConfig,
        )
        from bedrock_agentcore.memory.integrations.strands.session_manager import (  # noqa: E402
            AgentCoreMemorySessionManager as RealMemoryManager,
        )
except ImportError:
    RealMemoryManager = None

TOOL = "MeridianCatalog___price_range"
PAYLOAD = {"event": "concierge_turn", "traveler_id": "trv_meridian_demo",
           "conversation_id": "conv-1", "prompt": "What do Kyoto trips cost?"}
CEDAR = ("AuthorizeActionException - Tool Execution Denied: Tool call not allowed due to Cedar "
         "policy enforcement [No policy applies to the request (denied by default).]")
GATEWAY_REJECTED = "could not run (GatewayRejected): the Gateway answered HTTP"


def rpc(request_id, headers=None, **body):
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": request_id, **body}, headers=headers)


def gateway(on_call, stream_status=405):
    """A Gateway that lists one tool and answers ``tools/call`` with ``on_call(request_id)``.

    ``stream_status`` is what it answers to the client's ``GET`` stream and ``DELETE``.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return httpx.Response(stream_status)
        body = json.loads(request.content)
        method, request_id = body.get("method"), body.get("id")
        if method == "initialize":
            return rpc(request_id, result={
                "protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                "serverInfo": {"name": "gateway", "version": "1"}},
                       headers={"mcp-session-id": "session-1"})
        if method == "tools/list":
            return rpc(request_id, result={"tools": [{
                "name": TOOL, "description": "Price range",
                "inputSchema": {"type": "object",
                                "properties": {"destination": {"type": "string"}}}}]})
        if method == "tools/call":
            return on_call(request_id)
        return httpx.Response(202)

    return handler


def rejected(status, error_type):
    return lambda _id: httpx.Response(
        status, json={"message": "denied for 111122223333"},
        headers={"x-amzn-ErrorType": error_type})


def cedar(request_id):
    return rpc(request_id, error={"code": -32002, "message": CEDAR})


def is_error(request_id):
    return rpc(request_id, result={"isError": True, "content": [
        {"type": "text", "text": "An internal error occurred."}]})


def catalog_tool_failed(request_id):
    return rpc(request_id, result={"content": [
        {"type": "text", "text": json.dumps({"error": "catalog_tool_failed"})}]})


def client_class(handler):
    """The Runtime's ``MCPClient(url=..., auth_provider=...)`` call, served by ``handler``.

    The transport is the one Strands builds on mcp 2.x: an HTTPX client that carries the
    headers and the auth, handed to ``streamable_http_client``.
    """
    @asynccontextmanager
    async def transport(url, headers, auth):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=headers,
                                   auth=auth, timeout=httpx.Timeout(30, read=300))
        async with client, streamable_http_client(url, http_client=client) as streams:
            yield streams

    class MockGatewayClient(MCPClient):
        def __init__(self, url, auth_provider=None, headers=None):
            super().__init__(transport_callable=lambda: transport(url, headers, auth_provider))

    return MockGatewayClient


@pytest.fixture
def runtime(monkeypatch):
    module = load_runtime_main(monkeypatch, "meridian_runtime_gateway_rejections")
    module.SESSION = boto3.Session(aws_access_key_id="AKIAEXAMPLE", aws_secret_access_key="secret",
                                   region_name="us-east-1")
    module.real_memory_manager = module.memory_manager
    module.memory_manager = lambda *_args: None
    module.real_memory_span = module.memory_span
    module.memory_span = lambda *_args: module.activity("reasoning", "AgentCore Memory", "ok")
    return module


RUN_BOUND_SECONDS = 15
# Strands 1.57.2 stops the MCP client's background loop when the transport fails, and then
# schedules its close coroutine on that stopped loop, which warns that it was never awaited.
# The Runtime cannot await it; the turn ends by the poll or the deadline instead.
STOPPED_LOOP = pytest.mark.filterwarnings(
    "ignore:coroutine 'MCPClient.stop.<locals>._set_close_event' was never awaited:RuntimeWarning")


def run_turn(module, handler, model, payload=PAYLOAD):
    """The events of one turn; a turn that does not end fails the test after the bound."""
    module.MCPClient = client_class(handler)
    module.concierge_model = lambda: model

    async def collect():
        return [json.loads(item) async for item in module.invoke(payload, None)]

    return asyncio.run(asyncio.wait_for(collect(), RUN_BOUND_SECONDS))


def price_model(*after):
    return ScriptedModel(calls(TOOL, destination="Kyoto"), *after)


@STOPPED_LOOP
@pytest.mark.parametrize("status,error_type", [(401, "ExpiredTokenException"),
                                               (403, "AccessDeniedException")])
def test_a_gateway_rejection_ends_the_turn_as_a_setup_failure(runtime, status, error_type):
    model = price_model(says("I could not look that up."))

    events = run_turn(runtime, gateway(rejected(status, error_type)), model)

    [error] = [event for event in events if event["type"] == "error"]
    assert error["error_type"] == "ToolSetupError"
    assert error["message"].endswith(f"{GATEWAY_REJECTED} {status}.")
    assert "111122223333" not in json.dumps(events)
    assert model.calls == 1, "the model is not called again after the rejected call"
    assert events[-1] == error, "no result follows the error, so the audit row is an error"


@pytest.mark.parametrize("on_call,name", [(cedar, "Cedar refusal"), (is_error, "isError result"),
                                          (catalog_tool_failed, "catalog_tool_failed")])
def test_other_failures_keep_their_paths_and_the_model_answers(runtime, on_call, name):
    model = price_model(says("I could not look that up."))

    events = run_turn(runtime, gateway(on_call), model)

    assert [event for event in events if event["type"] == "error"] == [], name
    assert events[-1]["type"] == "result" and model.calls == 2


def test_a_cedar_refusal_is_recorded_with_its_decision_not_as_a_setup_failure(runtime):
    events = run_turn(runtime, gateway(cedar), price_model(says("That is not permitted.")))

    denied = [event for event in events if event.get("activity_type") == "security"]
    assert [event["title"] for event in denied] == ["Tool call denied by policy"]
    assert "ToolSetupError" not in json.dumps(events)


def recorder(*statuses):
    auth = gateway_auth.RecordingAuth()
    auth.rejections.extend(statuses)
    return auth


def test_a_recorded_rejection_stops_the_turn_with_the_status():
    stop = tool_errors.StopOnToolSetupError(recorder(403))
    ended = SimpleNamespace(end_turn=None)

    assert stop.note_rejection()
    stop.end_turn(ended)

    assert ended.end_turn == tool_errors.STOPPED_REPLY
    with pytest.raises(tool_errors.ToolSetupError, match=r"HTTP 403\.$"):
        stop.raise_if_stopped()


def test_no_recorded_rejection_leaves_the_turn_running():
    stop = tool_errors.StopOnToolSetupError(recorder())

    assert not stop.note_rejection()
    stop.raise_if_stopped()


def test_a_failed_tool_result_alone_never_stops_the_turn():
    stop = tool_errors.StopOnToolSetupError(recorder(401))
    event = SimpleNamespace(
        tool_use={"name": TOOL, "toolUseId": "t1", "input": {}}, exception=None,
        result={"status": "error", "toolUseId": "t1", "content": [{"text":
                "Tool execution failed: Connection to the MCP server was closed"}]})

    stop.after(event)

    assert stop.message is None, "only the recorded status stops a turn, never a result's text"


TOOL_CALL = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": TOOL}}


def answer_through(auth, status, method="POST", body=TOOL_CALL):
    """Send one request through ``auth`` to a Gateway that answers ``status``."""
    seen = {}

    def handler(request):
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(status)

    async def send():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), auth=auth) as client:
            await client.request(method, "https://gw.example/mcp",
                                 json=body if method == "POST" else None)

    asyncio.run(send())
    return seen


@pytest.mark.parametrize("status,expected", [(401, [401]), (403, [403]), (200, []), (404, []),
                                             (500, [])])
def test_only_a_401_or_403_is_recorded(status, expected):
    auth = gateway_auth.RecordingAuth()

    answer_through(auth, status)

    assert auth.rejections == expected and auth.rejected == bool(expected)


@pytest.mark.parametrize("method,body", [
    ("GET", None),
    ("DELETE", None),
    ("POST", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
    ("POST", {"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
    ("POST", {"jsonrpc": "2.0", "method": "notifications/initialized"}),
    ("POST", [1, 2]),
], ids=["stream GET", "session DELETE", "tools/list", "initialize", "notification",
        "not an object"])
@pytest.mark.parametrize("status", [401, 403])
def test_a_rejection_of_a_request_that_is_not_a_tool_call_is_not_recorded(
        method, body, status):
    auth = gateway_auth.RecordingAuth()

    answer_through(auth, status, method, body)

    assert auth.rejections == [] and not auth.rejected


def test_a_rejection_of_a_body_that_is_not_json_is_not_recorded():
    auth = gateway_auth.RecordingAuth()

    async def send():
        handler = lambda _request: httpx.Response(401)  # noqa: E731
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), auth=auth) as client:
            await client.post("https://gw.example/mcp", content=b"\xff not json")

    asyncio.run(send())

    assert auth.rejections == []


async def slow_cedar(request_id):
    """Answers after the Runtime's rejection poll has looked at the recorder at least twice."""
    await asyncio.sleep(0.8)
    return cedar(request_id)


@STOPPED_LOOP
@pytest.mark.parametrize("stream_status", [401, 403])
def test_a_rejected_stream_or_session_request_does_not_end_the_turn(runtime, stream_status):
    model = price_model(says("Here is the range."))

    events = run_turn(runtime, gateway(slow_cedar, stream_status=stream_status), model)

    assert [event for event in events if event["type"] == "error"] == []
    assert events[-1]["type"] == "result" and model.calls == 2


def test_the_recorder_still_signs_with_the_wrapped_sigv4_auth():
    session = boto3.Session(aws_access_key_id="AKIAEXAMPLE", aws_secret_access_key="secret",
                            region_name="us-east-1")
    auth = gateway_auth.RecordingAuth(gateway_auth.GatewaySigV4(session, "us-east-1"))

    seen = answer_through(auth, 401)

    assert seen["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIAEXAMPLE/")
    assert auth.rejections == [401]


def test_both_auth_modes_get_a_recorder():
    signed = {"auth_provider": httpx.BasicAuth("a", "b")}
    iam, iam_recorder = gateway_auth.recording_arguments(signed)
    bearer = {"headers": {"Authorization": "Bearer t"}}
    jwt, jwt_recorder = gateway_auth.recording_arguments(bearer)

    assert iam["auth_provider"] is iam_recorder and iam_recorder.inner is not None
    assert jwt["auth_provider"] is jwt_recorder and jwt_recorder.inner is None
    assert jwt["headers"] == {"Authorization": "Bearer t"}


# ------------------------------------------------------------------ the turn watchdog

HOLD_TOOL = "MeridianHolds___create_courtesy_hold"
IDLE = 1.5
CAP = 30.0
HOLD_PAYLOAD = {**PAYLOAD, "hold_confirmed": True, "hold_target": {
    "package_id": "pkg-1", "duration": "7 days", "travelers": 2, "unit_price_cents": 100000}}
HELD = {"hold": {"bookingId": "bk_77", "status": "held", "packageId": "pkg-1",
                 "duration": "7 days", "travelers": 2, "totalAmount": 2000,
                 "expiresAt": "2026-12-01T00:00:00Z", "seatsRemaining": 3}}


async def never_answers(_request_id):
    await asyncio.sleep(3600)


def connection_dropped(_request_id):
    raise httpx.ConnectError("connection dropped")


def hold_placed(request_id):
    return rpc(request_id, result={"content": [{"type": "text", "text": json.dumps(HELD)}]})


def routed(routes, hang_on=None):
    """A Gateway that lists every route and answers ``tools/call`` by the tool's name.

    ``hang_on`` names the one method (``tools/list``) that never answers.
    """
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method != "POST":
            return httpx.Response(405)
        body = json.loads(request.content)
        method, request_id = body.get("method"), body.get("id")
        if method == hang_on:
            return never_answers(request_id)
        if method == "initialize":
            return rpc(request_id, result={
                "protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                "serverInfo": {"name": "gateway", "version": "1"}})
        if method == "tools/list":
            return rpc(request_id, result={"tools": [{
                "name": name, "description": name,
                "inputSchema": {"type": "object", "properties": {}}} for name in routes]})
        if method == "tools/call":
            return routes[body["params"]["name"]](request_id)
        return httpx.Response(202)

    return handler


def deadline_error(placed=""):
    return f"The concierge did not finish in time. Please try again.{placed}"


@pytest.fixture
def short_deadline(runtime, monkeypatch):
    """The Runtime with an idle limit of 1.5 s and a hard cap far above any test."""
    monkeypatch.setattr(runtime, "IDLE_LIMIT_SECONDS", IDLE)
    monkeypatch.setattr(runtime, "MAX_TURN_SECONDS", CAP)
    return runtime


@STOPPED_LOOP
@pytest.mark.parametrize("on_call", [never_answers, connection_dropped],
                         ids=["never answers", "connection dropped"])
def test_a_tool_call_that_cannot_finish_ends_the_turn_at_the_deadline(short_deadline, on_call):
    model = price_model(says("unreachable"))

    events = run_turn(short_deadline, gateway(on_call), model)

    [error] = [event for event in events if event["type"] == "error"]
    assert error == {"type": "error", "error_type": "TurnTimedOut", "message": deadline_error()}
    assert events[-1] == error and model.calls == 1


def test_a_tool_listing_that_never_answers_ends_the_turn_at_the_deadline(short_deadline):
    events = run_turn(short_deadline, routed({TOOL: cedar}, hang_on="tools/list"),
                      price_model(says("unreachable")))

    assert events[-1]["error_type"] == "TurnTimedOut"
    assert [event["type"] for event in events] == ["activity", "error"]


def test_a_confirmed_hold_that_never_answers_ends_the_turn_at_the_deadline(short_deadline):
    model = ScriptedModel(says("unreachable"))

    events = run_turn(short_deadline, routed({HOLD_TOOL: never_answers, TOOL: cedar}), model,
                      HOLD_PAYLOAD)

    assert events[-1] == {"type": "error", "error_type": "TurnTimedOut",
                          "message": deadline_error()}
    assert model.calls == 0, "the model never narrates a hold that has no outcome"


def test_a_hold_the_gateway_accepted_before_the_deadline_is_reported(short_deadline):
    model = ScriptedModel(calls_each((HOLD_TOOL, {"packageId": "pkg-1"}),
                                     (TOOL, {"destination": "Kyoto"})), says("unreachable"))

    events = run_turn(short_deadline, routed({HOLD_TOOL: hold_placed, TOOL: never_answers}), model)

    [hold] = [event for event in events if event["type"] == "hold"]
    assert hold["hold"]["bookingId"] == "bk_77" and hold["policyDecision"] == "allow"
    assert events.index(hold) < len(events) - 1
    assert events[-1]["message"] == deadline_error(
        placed=" Hold bk_77 was placed before the turn ended.")


def test_a_turn_inside_the_deadline_is_not_touched(short_deadline):
    events = run_turn(short_deadline, gateway(cedar), price_model(says("That is not permitted.")))

    assert events[-1]["type"] == "result"


def test_the_one_call_that_blocks_the_loop_leaves_room_before_the_callers_read_timeout(runtime):
    """The backend gives up on a Runtime silent for 45 s; the fixed answer must beat that."""
    from backend.agentcore.runtime_https import TIMEOUT

    ending = runtime.CANCEL_GRACE_SECONDS + runtime.SETTLE_SECONDS + runtime.CLOSE_SECONDS
    write = runtime.IDLE_LIMIT_SECONDS + runtime.CLOSE_SECONDS
    assert TIMEOUT.read == 45.0 == runtime.CALLER_READ_SECONDS
    assert runtime.IDLE_LIMIT_SECONDS + ending + 3 <= TIMEOUT.read
    assert write + 4 <= TIMEOUT.read
    assert runtime.KEEPALIVE_SECONDS < runtime.IDLE_LIMIT_SECONDS < runtime.MAX_TURN_SECONDS


def test_one_memory_call_is_bounded_by_its_attempts_timeouts_and_backoff(runtime):
    """Three attempts of 2 s + 4 s, and the standard mode's backoff of at most 1 s then 2 s."""
    config = runtime.memory_client_config()

    per_attempt = config.connect_timeout + config.read_timeout
    assert config.retries == {"mode": "standard", "total_max_attempts": runtime.MEMORY_ATTEMPTS}
    assert runtime.MEMORY_ATTEMPTS == 3 and per_attempt == 6
    assert runtime.MEMORY_CALL_SECONDS == runtime.MEMORY_ATTEMPTS * per_attempt + 1 + 2 == 21


def test_the_hard_cap_stays_inside_the_runtime_request_limit(runtime):
    """AgentCore Runtime ends a synchronous request after 15 minutes (service quotas)."""
    assert 60 <= runtime.MAX_TURN_SECONDS <= 15 * 60 // 2


class RecordingModel(ScriptedModel):
    """A scripted model that keeps the history each request carried."""

    def __init__(self, *replies):
        super().__init__(*replies)
        self.requests = []

    async def stream(self, messages, *args, **kwargs):
        self.requests.append(copy.deepcopy(messages))
        async for event in super().stream(messages, *args, **kwargs):
            yield event


def assert_usable(messages):
    """Every toolUse is answered by the next message's toolResult, and the roles alternate."""
    roles = [message["role"] for message in messages]
    assert all(left != right for left, right in zip(roles, roles[1:], strict=False)), roles
    for index, message in enumerate(messages):
        used = sorted(block["toolUse"]["toolUseId"] for block in message["content"]
                      if "toolUse" in block)
        if used:
            assert index + 1 < len(messages), "the history ends in a toolUse"
            answered = sorted(block["toolResult"]["toolUseId"]
                              for block in messages[index + 1]["content"] if "toolResult" in block)
            assert answered == used


@pytest.fixture
def saved_session(runtime, tmp_path):
    def manager(*_args):
        return FileSessionManager(session_id="conv-1", storage_dir=str(tmp_path))

    runtime.memory_manager = manager
    return lambda: [saved.message for saved in manager().session_repository.list_messages(
        "conv-1", "default")]


@STOPPED_LOOP
@pytest.mark.parametrize("first,why", [
    (gateway(never_answers), "deadline"),
    (gateway(rejected(401, "ExpiredTokenException")), "rejection poll"),
], ids=["deadline", "rejection poll"])
def test_a_turn_ended_early_leaves_a_history_the_next_turn_can_use(
        short_deadline, saved_session, first, why):
    run_turn(short_deadline, first, price_model(says("unreachable")))

    assert_usable(saved_session())
    second = RecordingModel(says("Welcome back."))
    events = run_turn(short_deadline, gateway(cedar), second)

    assert events[-1]["type"] == "result", why
    assert_usable(second.requests[0])


class ChattyModel(ScriptedModel):
    """A model that streams ``chunks`` pieces ``pause`` seconds apart (``None``: forever)."""

    def __init__(self, chunks, pause, kind="text"):
        super().__init__()
        self.chunks, self.pause, self.kind = chunks, pause, kind

    async def stream(self, _messages, *_args, **_kwargs):
        self.calls += 1
        yield {"messageStart": {"role": "assistant"}}
        sent = 0
        while self.chunks is None or sent < self.chunks:
            await asyncio.sleep(self.pause)
            sent += 1
            if self.kind == "text":
                yield {"contentBlockDelta": {"delta": {"text": f"piece {sent} "}}}
            else:
                yield {"contentBlockDelta": {"delta": {
                    "reasoningContent": {"text": f"thought {sent} "}}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}


def timed_turn(module, handler, model, payload=PAYLOAD):
    """The events of one turn with the seconds at which each arrived."""
    module.MCPClient = client_class(handler)
    module.concierge_model = lambda: model

    async def collect():
        started, seen = asyncio.get_running_loop().time(), []
        async for item in module.invoke(payload, None):
            seen.append((asyncio.get_running_loop().time() - started, json.loads(item)))
        return seen

    return asyncio.run(asyncio.wait_for(collect(), RUN_BOUND_SECONDS))


def test_a_turn_that_keeps_streaming_runs_past_the_idle_limit(short_deadline):
    """Six pieces 0.5 s apart take 3 s, twice the 1.5 s idle limit, with no gap over 0.5 s."""
    model = ChattyModel(6, 0.5)

    seen = timed_turn(short_deadline, gateway(cedar), model)

    assert seen[-1][1]["type"] == "result" and seen[-1][0] > IDLE * 1.5
    assert [e for _, e in seen if e["type"] == "error"] == []


def test_a_turn_that_goes_silent_ends_at_the_idle_limit(short_deadline):
    seen = timed_turn(short_deadline, gateway(cedar), ChattyModel(1, 3600))

    at, error = seen[-1]
    assert error == {"type": "error", "error_type": "TurnTimedOut", "message": deadline_error()}
    assert IDLE <= at < IDLE + 4


def test_a_turn_that_keeps_producing_progress_ends_at_the_hard_cap(runtime, monkeypatch):
    monkeypatch.setattr(runtime, "IDLE_LIMIT_SECONDS", 1.5)
    monkeypatch.setattr(runtime, "MAX_TURN_SECONDS", 3.0)

    seen = timed_turn(runtime, gateway(cedar), ChattyModel(None, 0.2))

    at, error = seen[-1]
    assert error["error_type"] == "TurnTimedOut" and error["message"] == deadline_error()
    assert 3.0 <= at < 3.0 + 4, "progress never extends the turn past the cap"


def test_unseen_progress_is_answered_with_keepalives_that_do_not_count_as_progress(
        short_deadline, monkeypatch):
    """Model reasoning is progress the caller never sees; it gets a keepalive every 0.4 s."""
    monkeypatch.setattr(short_deadline, "KEEPALIVE_SECONDS", 0.4)

    seen = timed_turn(short_deadline, gateway(cedar), ChattyModel(6, 0.5, kind="reasoning"))

    times = [at for at, _ in seen]
    kinds = [event["type"] for _, event in seen]
    assert kinds.count("keepalive") >= 3 and kinds[-1] == "result"
    assert max(b - a for a, b in zip(times, times[1:], strict=False)) < 0.4 + 0.4


@STOPPED_LOOP
def test_keepalives_never_keep_a_silent_tool_call_alive(short_deadline, monkeypatch):
    monkeypatch.setattr(short_deadline, "KEEPALIVE_SECONDS", 0.4)

    seen = timed_turn(short_deadline, gateway(never_answers), price_model(says("unreachable")))

    at, error = seen[-1]
    assert error["error_type"] == "TurnTimedOut" and at < IDLE + 4
    assert any(event["type"] == "keepalive" for _, event in seen)


def test_a_hung_agent_build_ends_the_turn_at_the_idle_limit(short_deadline):
    release = threading.Event()
    short_deadline.memory_manager = lambda *_args: release.wait(3)

    seen = timed_turn(short_deadline, gateway(cedar), price_model(says("unreachable")))

    at, error = seen[-1]
    assert error == {"type": "error", "error_type": "TurnTimedOut", "message": deadline_error()}
    assert IDLE <= at < 3, "the turn ended at the idle limit, not when the build returned"


def test_a_hung_gateway_client_start_ends_the_turn_at_the_idle_limit(short_deadline):
    base = client_class(gateway(cedar))

    closed = threading.Event()

    class HungStart(base):
        def __enter__(self):
            threading.Event().wait(3)
            return super().__enter__()

        def __exit__(self, *exc):
            try:
                return super().__exit__(*exc)
            finally:
                closed.set()

    short_deadline.MCPClient = HungStart
    short_deadline.concierge_model = lambda: price_model(says("unreachable"))

    async def collect():
        started = asyncio.get_running_loop().time()
        items = [json.loads(item) async for item in short_deadline.invoke(PAYLOAD, None)]
        return items, asyncio.get_running_loop().time() - started

    events, took = asyncio.run(asyncio.wait_for(collect(), RUN_BOUND_SECONDS))

    assert events[-1] == {"type": "error", "error_type": "TurnTimedOut",
                          "message": deadline_error()}
    assert IDLE <= took < 2.5, "the turn ended at the idle limit, not when the start returned"
    assert closed.wait(RUN_BOUND_SECONDS), "the start that finished late was closed"


def test_the_backends_reader_ignores_a_keepalive_event():
    from backend.agentcore.runtime import (
        RuntimeDecision,
        _apply_event,
        _forward_runtime_events,
    )

    frames = [b'data: {"type": "keepalive"}\n\n',
              b'data: {"type": "result", "message": "Here you go."}\n\n']
    decision = RuntimeDecision(
        runtime_arn="arn", runtime_session_id="s", qualifier="DEFAULT", message="",
        recommended_package_ids=[], follow_ups=[])

    for event in _forward_runtime_events({"response": iter(frames)}):
        _apply_event(decision, event)

    assert decision.message == "Here you go." and decision.activities == []


# ------------------------------------------- which RuntimeError a turn reports (run's except)


def failing_client(handler, *, exit_error=None, listing_error=None):
    """The mock Gateway client, with an error raised on exit or when the tools are listed."""
    base = client_class(handler)

    class FailingClient(base):
        def __exit__(self, *exc):
            super().__exit__(*exc)
            if exit_error:
                raise exit_error

        def list_tools_sync(self, *args, **kwargs):
            if listing_error:
                raise listing_error
            return super().list_tools_sync(*args, **kwargs)

    return FailingClient


def events_with(runtime, client):
    runtime.concierge_model = lambda: price_model(says("done"))
    runtime.MCPClient = client

    async def collect():
        return [json.loads(item) async for item in runtime.invoke(PAYLOAD, None)]

    return asyncio.run(asyncio.wait_for(collect(), RUN_BOUND_SECONDS))


@STOPPED_LOOP
def test_a_different_runtime_error_on_exit_is_replaced_by_the_recorded_rejection(runtime):
    client = failing_client(gateway(rejected(401, "ExpiredTokenException")),
                            exit_error=RuntimeError("some other closing error"))

    [error] = [event for event in events_with(runtime, client) if event["type"] == "error"]

    assert error["error_type"] == "ToolSetupError" and error["message"].endswith("HTTP 401.")


def test_a_runtime_error_with_no_recorded_rejection_passes_through_unchanged(runtime):
    client = failing_client(gateway(cedar), listing_error=RuntimeError("listing exploded"))

    [error] = [event for event in events_with(runtime, client) if event["type"] == "error"]

    assert error == {"type": "error", "error_type": "RuntimeError", "message": "listing exploded"}


def test_a_rejection_recorded_as_the_agent_finishes_still_ends_the_turn_as_a_setup_failure(
        runtime, monkeypatch):
    """A turn faster than the poll interval is checked once more when the agent is done."""
    real = gateway_auth.recording_arguments

    def already_rejected(arguments):
        arguments, recorder_ = real(arguments)
        recorder_.rejections.append(401)
        return arguments, recorder_

    monkeypatch.setattr(runtime, "recording_arguments", already_rejected)

    events = run_turn(runtime, gateway(cedar), price_model(says("That is not permitted.")))

    assert events[-1]["error_type"] == "ToolSetupError"
    assert events[-1]["message"].endswith(f"{GATEWAY_REJECTED} 401.")


# ------------------------------------- Memory calls, a caller that leaves, and abandoned work

CONVERSATION = PAYLOAD["conversation_id"]
CLOSING_REPLY = {"role": "assistant", "content": [{"text": turn_watchdog.INTERRUPTED_REPLY}]}


@pytest.fixture
def memory_runtime(monkeypatch):
    """The Runtime's own ``main.py`` with its Memory config classes stood in by plain values."""
    module = load_runtime_main(monkeypatch, "meridian_runtime_memory")
    module.AgentCoreMemoryConfig = lambda **kwargs: SimpleNamespace(**kwargs)
    module.RetrievalConfig = lambda **kwargs: kwargs
    return module


def until(condition, seconds=RUN_BOUND_SECONDS):
    """Poll ``condition`` from a test thread; fail the test if it never holds."""
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "the condition never held"
        time.sleep(0.02)


def test_every_memory_client_gets_short_timeouts_bounded_retries_and_its_own_session(
        memory_runtime):
    built, clients = [], []

    class Manager:
        def __init__(self, **kwargs):
            built.append(kwargs)
            self.memory_client = SimpleNamespace(list_events=lambda **_: {"events": []})

    class Session:
        def client(self, service, **kwargs):
            clients.append((self, service, kwargs))
            return SimpleNamespace(list_events=lambda **_: {"events": []})

    memory_runtime.AgentCoreMemorySessionManager = Manager
    memory_runtime.memory_session = Session

    memory_runtime.memory_manager("trv_1", "conv-1")
    memory_runtime.memory_span("trv_1", "conv-1")
    memory_runtime.memory_span("trv_1", "conv-1")

    sessions = [built[0]["boto_session"], clients[0][0], clients[1][0]]
    for config in [built[0]["boto_client_config"], clients[0][2]["config"],
                   clients[1][2]["config"]]:
        assert config.connect_timeout == memory_runtime.MEMORY_CONNECT_SECONDS
        assert config.read_timeout == memory_runtime.MEMORY_READ_SECONDS
        assert config.retries == {"mode": "standard", "total_max_attempts": 3}
    assert built[0]["agentcore_memory_config"].async_mode is True
    assert len({id(session) for session in sessions}) == 3
    assert memory_runtime.SESSION not in sessions, "no session is shared across threads"


@STOPPED_LOOP
def test_a_dropped_connection_on_the_confirmed_write_is_reported_and_ends_the_turn(
        short_deadline):
    events = run_turn(short_deadline, routed({HOLD_TOOL: connection_dropped, TOOL: cedar}),
                      ScriptedModel(says("The hold could not be placed.")), HOLD_PAYLOAD)

    [hold] = [event for event in events if event["type"] == "hold"]
    assert hold["hold"] is None and "Connection to the MCP server was closed" in hold["error"]
    assert not [event for event in events if event["type"] == "result"]
    assert events[-1] == {"type": "error", "error_type": "RuntimeError",
                          "message": "Connection to the MCP server was closed"}


class WatchedModel(ChattyModel):
    """A model that notes when its stream is cancelled."""

    cancelled = False

    async def stream(self, *args, **kwargs):
        try:
            async for event in super().stream(*args, **kwargs):
                yield event
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def closing_client(handler, closed, before=None, reached=None):
    """The mock Gateway client: ``closed`` gets a mark on close; ``before`` gates its start.

    ``reached`` is set when the start begins, so a test knows the start is waiting on ``before``.
    """
    base = client_class(handler)

    class ClosingClient(base):
        def __enter__(self):
            if reached is not None:
                reached.set()
            if before is not None:
                before.wait(RUN_BOUND_SECONDS)
            return super().__enter__()

        def __exit__(self, *exc):
            closed.append("closed")
            return super().__exit__(*exc)

    return ClosingClient


def test_a_caller_that_closes_the_stream_mid_turn_settles_the_turn(short_deadline, saved_session):
    closed, model = [], WatchedModel(None, 0.05)
    short_deadline.MCPClient = closing_client(gateway(cedar), closed)
    short_deadline.concierge_model = lambda: model

    async def scenario():
        stream, tokens = short_deadline.invoke(PAYLOAD, None), 0
        async for item in stream:
            tokens += json.loads(item)["type"] == "token"
            if tokens == 2:
                break
        await stream.aclose()

    asyncio.run(asyncio.wait_for(scenario(), RUN_BOUND_SECONDS))

    messages = saved_session()
    assert model.cancelled and closed == ["closed"]
    assert_usable(messages)
    assert messages[-1] == CLOSING_REPLY
    assert not short_deadline.REPAIRS.running(CONVERSATION)


def test_closing_the_stream_returns_only_after_the_history_is_saved(short_deadline, tmp_path):
    """The closing messages are on disk when ``aclose`` returns, however slow the save."""
    class SlowSave(FileSessionManager):
        def append_message(self, message, agent, **kwargs):
            time.sleep(0.2)
            super().append_message(message, agent, **kwargs)

    def manager(*_args):
        return SlowSave(session_id="conv-1", storage_dir=str(tmp_path))

    short_deadline.memory_manager = manager
    short_deadline.MCPClient = closing_client(gateway(cedar), [])
    short_deadline.concierge_model = lambda: WatchedModel(None, 0.05)

    async def scenario():
        stream, tokens = short_deadline.invoke(PAYLOAD, None), 0
        async for item in stream:
            tokens += json.loads(item)["type"] == "token"
            if tokens == 2:
                break
        await stream.aclose()

    asyncio.run(asyncio.wait_for(scenario(), RUN_BOUND_SECONDS))

    rows = manager().session_repository.list_messages("conv-1", "default")
    assert rows[-1].message == CLOSING_REPLY


def test_a_gateway_client_that_starts_after_the_caller_left_is_closed(short_deadline):
    closed, release, reached = [], threading.Event(), threading.Event()
    short_deadline.MCPClient = closing_client(
        gateway(cedar), closed, before=release, reached=reached)
    short_deadline.concierge_model = lambda: ScriptedModel(says("unreachable"))

    async def consume():
        async for _item in short_deadline.invoke(PAYLOAD, None):
            pass

    async def scenario():
        consumer = asyncio.create_task(consume())
        assert await asyncio.to_thread(reached.wait, RUN_BOUND_SECONDS), "the start never began"
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
        assert closed == [], "the client has not started yet"
        release.set()
        await asyncio.to_thread(until, lambda: closed)

    try:
        asyncio.run(asyncio.wait_for(scenario(), RUN_BOUND_SECONDS))
    finally:
        release.set()

    assert closed == ["closed"]


def open_history():
    """An agent whose cancelled turn left a toolUse unanswered."""
    return SimpleNamespace(messages=[
        {"role": "user", "content": [{"text": "hello"}]},
        {"role": "assistant", "content": [{"toolUse": {"toolUseId": "t1", "name": "x",
                                                         "input": {}}}]}])


class GatedSession:
    """A session manager whose first save waits on ``gate``."""

    def __init__(self):
        self.gate, self.appended = threading.Event(), []

    def append_message(self, message, _agent):
        self.gate.wait(5)
        self.appended.append(message)


def test_a_repair_that_outlives_its_wait_is_stopped_before_it_appends_again(caplog):
    repairs = turn_work.HistoryRepairs(turn_work.OffLoopExecutor(2))
    session, agent = GatedSession(), open_history()
    repairs.start(CONVERSATION, lambda keep_going: turn_watchdog.settle_history(
        agent, session, keep_going))

    try:
        with caplog.at_level(logging.WARNING):
            finished = asyncio.run(repairs.finish(CONVERSATION, 0.1))
    finally:
        session.gate.set()
    until(lambda: not repairs.running(CONVERSATION))

    assert finished is False
    assert len(session.appended) == 1, "the closing reply was not appended after the stop"
    assert "history may have been left open" in caplog.text and CONVERSATION in caplog.text
    assert "stopped after 1 of 2 messages" in caplog.text


def test_a_second_repair_of_a_conversation_stops_the_first_and_replaces_it(caplog):
    repairs = turn_work.HistoryRepairs(turn_work.OffLoopExecutor(2))
    gate, seen = threading.Event(), []

    def first(keep_going):
        gate.wait(5)
        seen.append(keep_going())
        return 0

    repairs.start(CONVERSATION, first)
    try:
        with caplog.at_level(logging.WARNING):
            repairs.start(CONVERSATION, lambda _keep_going: 0)
        until(lambda: not repairs.running(CONVERSATION))
    finally:
        gate.set()
    until(lambda: seen)

    assert seen == [False]
    assert "a newer repair replaced it" in caplog.text and CONVERSATION in caplog.text


def test_the_next_turn_stops_an_earlier_repair_that_is_still_running(
        short_deadline, monkeypatch, caplog):
    monkeypatch.setattr(short_deadline, "SETTLE_SECONDS", 0.2)
    gate, seen = threading.Event(), []

    def slow_repair(keep_going):
        gate.wait(5)
        seen.append(keep_going())
        return 0

    short_deadline.REPAIRS.start(CONVERSATION, slow_repair)
    try:
        with caplog.at_level(logging.WARNING):
            events = run_turn(short_deadline, gateway(cedar), ScriptedModel(says("Welcome back.")))
    finally:
        gate.set()
    until(lambda: seen)

    assert events[-1]["type"] == "result" and seen == [False]
    assert "history may have been left open" in caplog.text


def history_turn(conversation_id="conv-9"):
    return SimpleNamespace(context=SimpleNamespace(conversation_id=conversation_id),
                           history_closed=False)


def test_a_history_that_cannot_be_repaired_is_logged_with_its_session(runtime, caplog):
    session, agent = GatedSession(), open_history()
    still_running = SimpleNamespace(done=lambda: False)

    with caplog.at_level(logging.WARNING):
        asyncio.run(runtime.close_history(history_turn(), agent, session, still_running))

    assert "session conv-9 was left open" in caplog.text
    assert session.appended == [] and not runtime.REPAIRS.running("conv-9")


def test_a_history_is_repaired_when_the_ended_task_is_done(runtime):
    session, agent = GatedSession(), open_history()
    session.gate.set()
    turn = history_turn()

    asyncio.run(runtime.close_history(turn, agent, session, SimpleNamespace(done=lambda: True)))

    assert [message["role"] for message in session.appended] == ["user", "assistant"]
    assert turn.history_closed


def test_the_executor_logs_when_every_worker_is_busy(caplog):
    executor, gate = turn_work.OffLoopExecutor(1), threading.Event()
    try:
        with caplog.at_level(logging.WARNING):
            executor.submit(gate.wait, 5)
            waiting = executor.submit(lambda: "later")
        assert executor.busy == 2 and "all 1 off-loop workers are busy" in caplog.text
    finally:
        gate.set()
    assert waiting.result(timeout=3) == "later"
    until(lambda: executor.busy == 0)


def test_a_hung_gateway_close_holds_one_worker_and_starves_nothing_else(
        short_deadline, monkeypatch):
    monkeypatch.setattr(short_deadline, "CLOSE_SECONDS", 0.1)
    release, closed = threading.Event(), []
    hung = closing_client(gateway(cedar), closed)

    class HungClose(hung):
        def __exit__(self, *exc):
            release.wait(5)
            return super().__exit__(*exc)

    async def turn():
        return [json.loads(item) async for item in short_deadline.invoke(PAYLOAD, None)]

    async def scenario():
        short_deadline.MCPClient = HungClose
        short_deadline.concierge_model = lambda: ScriptedModel(says("First."))
        first = await turn()
        held = short_deadline.OFFLOOP.busy
        later = await asyncio.wait_for(asyncio.to_thread(lambda: "free"), 1)
        short_deadline.MCPClient = closing_client(gateway(cedar), closed)
        short_deadline.concierge_model = lambda: ScriptedModel(says("Second."))
        return first, held, later, await turn()

    try:
        first, held, later, second = asyncio.run(asyncio.wait_for(scenario(), RUN_BOUND_SECONDS))
    finally:
        release.set()

    assert first[-1]["type"] == "result" and second[-1]["type"] == "result"
    assert held == 1 and later == "free"


# ------------------------- the Runtime's own Memory session manager (bedrock-agentcore 1.24.0)

needs_sdk = pytest.mark.skipif(
    RealMemoryManager is None,
    reason="bedrock-agentcore is the Concierge Runtime's own dependency, not the backend venv's")


class DataPlane:
    """The ``bedrock-agentcore`` boto3 client as AgentCore Memory answers it, with a call log."""

    def __init__(self):
        self.calls = []

    def create_event(self, **params):
        self.calls.append(("create_event", params))
        return {"event": {"eventId": f"evt-{len(self.calls)}", "memoryId": params["memoryId"],
                          "actorId": params["actorId"], "sessionId": params["sessionId"]}}

    def list_events(self, **params):
        self.calls.append(("list_events", params))
        return {"events": []}

    def retrieve_memory_records(self, **params):
        self.calls.append(("retrieve_memory_records", params))
        return {"memoryRecordSummaries": []}

    def names(self):
        return [name for name, _params in self.calls]


@pytest.fixture
def short_deadline_sdk(sdk_runtime, monkeypatch):
    """``sdk_runtime`` with the idle limit of ``short_deadline``."""
    monkeypatch.setattr(sdk_runtime, "IDLE_LIMIT_SECONDS", IDLE)
    monkeypatch.setattr(sdk_runtime, "MAX_TURN_SECONDS", CAP)
    return sdk_runtime


class StubSession:
    """A boto3 session whose clients are the stubbed data plane; it keeps each client's config."""

    region_name = "us-east-1"

    def __init__(self, plane):
        self.plane, self.configs = plane, {}

    def client(self, service, **kwargs):
        self.configs[service] = kwargs.get("config")
        return self.plane if service == "bedrock-agentcore" else SimpleNamespace()


@pytest.fixture
def sdk_runtime(runtime, monkeypatch):
    """The Runtime with the installed AgentCore Memory session manager over stubbed clients."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    runtime.AgentCoreMemorySessionManager = RealMemoryManager
    runtime.AgentCoreMemoryConfig = RealMemoryConfig
    runtime.RetrievalConfig = RealRetrievalConfig
    runtime.MEMORY_ID = "mem-0123456789"
    runtime.data_plane = DataPlane()
    runtime.stub_session = StubSession(runtime.data_plane)
    runtime.memory_session = lambda: runtime.stub_session
    runtime.memory_manager = runtime.real_memory_manager
    return runtime


@needs_sdk
def test_the_session_manager_builds_its_clients_with_the_runtimes_memory_settings(sdk_runtime):
    sdk_runtime.memory_manager("trv_1", "conv-1")

    configs = sdk_runtime.stub_session.configs
    assert set(configs) == {"bedrock-agentcore", "bedrock-agentcore-control"}
    for config in configs.values():
        assert config.connect_timeout == sdk_runtime.MEMORY_CONNECT_SECONDS
        assert config.read_timeout == sdk_runtime.MEMORY_READ_SECONDS
        assert config.retries == {"mode": "standard", "total_max_attempts": 3}


@needs_sdk
def test_a_memory_restore_that_succeeds_keeps_the_ok_memory_span(sdk_runtime):
    sdk_runtime.memory_span = sdk_runtime.real_memory_span

    events = run_turn(sdk_runtime, gateway(cedar), ScriptedModel(says("Welcome back.")))

    [span] = [event for event in events if event["type"] == "activity"
              and event["title"].startswith("AgentCore Memory")]
    assert span["title"] == "AgentCore Memory: session restored"
    assert span["telemetry"]["status"] == "ok"


@needs_sdk
@STOPPED_LOOP
def test_a_turn_ended_early_closes_the_history_through_the_agentcore_session_manager(
        short_deadline_sdk):
    run_turn(short_deadline_sdk, gateway(never_answers), price_model(says("unreachable")))

    saved = [params for name, params in short_deadline_sdk.data_plane.calls
             if name == "create_event"]
    texts = json.dumps(saved, default=str)
    assert turn_watchdog.INTERRUPTED_RESULT in texts and turn_watchdog.INTERRUPTED_REPLY in texts


@needs_sdk
def test_one_message_added_makes_no_more_memory_calls_than_the_arithmetic_assumes(
        sdk_runtime, monkeypatch):
    """The bound on a blocked loop multiplies one call's timeout by this count."""
    from strands.hooks.registry import HookRegistry

    plane, per_message = sdk_runtime.data_plane, []
    invoke = HookRegistry.invoke_callbacks_async

    async def counted(self, event):
        before = len(plane.calls)
        result = await invoke(self, event)
        if type(event).__name__ == "MessageAddedEvent":
            per_message.append(plane.names()[before:])
        return result

    monkeypatch.setattr(HookRegistry, "invoke_callbacks_async", counted)

    events = run_turn(sdk_runtime, gateway(cedar), price_model(says("That is not permitted.")))

    assert events[-1]["type"] == "result" and len(per_message) == 4
    assert max(len(calls_) for calls_ in per_message) == sdk_runtime.MEMORY_CALLS_PER_MESSAGE


class LoopWatch:
    """Measures how long the event loop goes without running a 20 ms timer."""

    def __init__(self):
        self.longest, self._task = 0.0, None

    async def __aenter__(self):
        async def tick():
            last = time.perf_counter()
            while True:
                await asyncio.sleep(0.02)
                now = time.perf_counter()
                self.longest, last = max(self.longest, now - last), now

        self._task = asyncio.create_task(tick())
        return self

    async def __aexit__(self, *_exc):
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)


class HangingWrites(DataPlane):
    """A data plane whose ``create_event`` waits on ``gate`` (at most 5 s) for chosen writes."""

    def __init__(self, hang_when=lambda params: True):
        super().__init__()
        self.gate, self.hang_when, self.hung = threading.Event(), hang_when, 0

    def create_event(self, **params):
        if self.hang_when(params):
            self.hung += 1
            self.calls.append(("create_event", params))
            self.gate.wait(5)
            return {"event": {"eventId": f"evt-{len(self.calls)}"}}
        return super().create_event(**params)


async def collect_events(module, model, payload=PAYLOAD):
    module.MCPClient = client_class(gateway(cedar))
    module.concierge_model = lambda: model
    return [json.loads(item) async for item in module.invoke(payload, None)]


@needs_sdk
def test_a_hung_memory_write_leaves_the_loop_free_and_the_watchdog_and_keepalive_running(
        short_deadline_sdk, monkeypatch):
    module = short_deadline_sdk
    monkeypatch.setattr(module, "KEEPALIVE_SECONDS", 0.4)
    monkeypatch.setattr(module, "SETTLE_SECONDS", 0.3)
    module.data_plane = module.stub_session.plane = HangingWrites(
        lambda params: "Kyoto" in json.dumps(params, default=str))

    async def scenario():
        async with LoopWatch() as watch:
            try:
                events = await collect_events(module, ScriptedModel(says("unreachable")))
            finally:
                module.data_plane.gate.set()
        return events, watch.longest

    started = time.perf_counter()
    events, longest = asyncio.run(asyncio.wait_for(scenario(), RUN_BOUND_SECONDS))

    assert module.data_plane.hung >= 1
    assert {"type": "keepalive"} in events, "the keepalive kept running while Memory hung"
    assert events[-1]["type"] == "error" and "did not finish in time" in events[-1]["message"]
    assert time.perf_counter() - started < 4.5, "the watchdog ended the turn at its idle limit"
    assert longest < 0.5, f"the loop stalled for {longest:.2f} s"


class ThrottlingSession(StubSession):
    """A session whose ``bedrock-agentcore`` client is a real botocore client.

    The first ``throttles`` ``CreateEvent`` requests are answered with HTTP 429; the client's own
    retry configuration decides what happens next. Every other operation goes to the stub.
    """

    def __init__(self, plane, throttles):
        super().__init__(plane)
        self.throttles, self.requests = throttles, []
        self.boto = boto3.Session(
            aws_access_key_id="AKIAEXAMPLE", aws_secret_access_key="secret",
            region_name="us-east-1")
        self.boto.events.register("before-send.bedrock-agentcore.CreateEvent", self.answer)

    def answer(self, request, **_kwargs):
        from botocore.awsrequest import AWSResponse

        class Raw:
            def __init__(self, body):
                self.body = body

            def stream(self, **_kw):
                yield self.body

        self.requests.append(request)
        if len(self.requests) <= self.throttles:
            body = json.dumps({"__type": "ThrottlingException", "message": "slow down"})
            return AWSResponse(request.url, 429, {"content-type": "application/json"},
                               Raw(body.encode()))
        body = json.dumps({"event": {"eventId": "evt-ok", "memoryId": "mem-0123456789",
                                     "actorId": "trv_meridian_demo", "sessionId": "conv-1",
                                     "eventTimestamp": 1.7e9}})
        return AWSResponse(request.url, 200, {"content-type": "application/json"},
                           Raw(body.encode()))

    def client(self, service, **kwargs):
        if service != "bedrock-agentcore":
            return super().client(service, **kwargs)
        real = self.boto.client(service, config=kwargs.get("config"))
        stub = self.plane

        class Mixed:
            create_event = staticmethod(real.create_event)

            def __getattr__(self, name):
                return getattr(stub, name)

        self.configs[service] = kwargs.get("config")
        return Mixed()


@needs_sdk
def test_a_throttled_memory_write_is_retried_and_the_turn_finishes(short_deadline_sdk):
    module = short_deadline_sdk
    session = module.stub_session = ThrottlingSession(module.data_plane, throttles=1)
    module.memory_session = lambda: session

    events = run_turn(module, gateway(cedar), ScriptedModel(says("Welcome back.")))

    assert events[-1]["type"] == "result"
    assert len(session.requests) >= 2, "the first CreateEvent was throttled and sent again"
    tokens = [json.loads(request.body)["clientToken"] for request in session.requests[:2]]
    assert tokens[0] == tokens[1], "a retried CreateEvent carries the same idempotency token"


def recorded_history(plane):
    """What a restore reads: every created event, newest first, as the service lists them."""
    created = [params for name, params in plane.calls if name == "create_event"]
    events = [{"eventId": f"evt-{index}", "eventTimestamp": params["eventTimestamp"],
               "sessionId": params["sessionId"], "payload": params["payload"],
               "metadata": params.get("metadata", {})}
              for index, params in enumerate(created)]
    return sorted(events, key=lambda event: event["eventTimestamp"], reverse=True)


class ReplayPlane(HangingWrites):
    """A data plane that lists back what was written, newest first, as the service does.

    ``replay`` stands in for what was written when a restore should see only part of it.
    """

    replay = None

    def list_events(self, **params):
        self.calls.append(("list_events", params))
        events = recorded_history(self) if self.replay is None else self.replay
        filtered = json.dumps(params.get("filter", {}))
        wanted = next((state for state in ("SESSION", "AGENT") if state in filtered), None)
        return {"events": [
            event for event in events if event["sessionId"] == params["sessionId"]
            and event["metadata"].get("stateType", {}).get("stringValue") == wanted]}


def stored_messages(plane):
    from bedrock_agentcore.memory.integrations.strands.bedrock_converter import (
        AgentCoreMemoryConverter,
    )

    return [saved.message for saved in AgentCoreMemoryConverter.events_to_messages(
        recorded_history(plane))]


def cancelled_during_a_write(module, monkeypatch):
    """Run a turn whose toolUse write hangs until the watchdog cancelled the agent.

    Returns the data plane with what the turn and its repair wrote, the late write included.
    """
    monkeypatch.setattr(module, "SETTLE_SECONDS", 0.5)
    plane = module.stub_session.plane = module.data_plane = ReplayPlane(
        lambda params: "price_range" in json.dumps(params, default=str))
    module.MCPClient = client_class(gateway(never_answers))
    module.concierge_model = lambda: price_model(says("unreachable"))

    async def scenario():
        try:
            return [json.loads(item) async for item in module.invoke(PAYLOAD, None)]
        finally:
            await asyncio.sleep(0.2)
            plane.gate.set()
            await asyncio.sleep(0.5)

    events = asyncio.run(asyncio.wait_for(scenario(), RUN_BOUND_SECONDS))
    assert events[-1]["type"] == "error" and plane.hung == 1
    return plane


@needs_sdk
@STOPPED_LOOP
def test_a_write_that_lands_after_the_turn_was_cancelled_leaves_a_valid_history(
        short_deadline_sdk, monkeypatch):
    """The assistant's toolUse is still being written when the watchdog cancels the agent.

    The repair answers the toolUse at once. The late write carries the timestamp taken when it
    started, so it sorts before the repair's messages whichever lands first.

    A guard, not a proof of the async change: it also passes on the design before it. It fails
    if the repair writes the held toolUse message again.
    """
    plane = cancelled_during_a_write(short_deadline_sdk, monkeypatch)

    messages = stored_messages(plane)
    assert_usable(messages)
    uses = [block for message in messages for block in message["content"] if "toolUse" in block]
    assert len(uses) == 1, "the late write was saved once"
    assert messages[-1] == CLOSING_REPLY


def written_before(plane, later):
    """The events written so far whose message text does not contain ``later``."""
    return [event for event in recorded_history(plane)
            if later not in json.dumps(event, default=str)]


@needs_sdk
@STOPPED_LOOP
@pytest.mark.parametrize("missing,why", [
    ("price_range", "the late write has not landed yet"),
    (turn_watchdog.INTERRUPTED_RESULT, "the repair was stopped before it wrote"),
])
def test_a_restore_between_the_late_write_and_the_repair_still_sends_a_valid_history(
        short_deadline_sdk, monkeypatch, missing, why):
    """A guard, not a proof of the async change: both cases also pass on the previous design.

    They fail if the repair writes the held toolUse message again or the restore sends a
    toolUse without its toolResult.
    """
    module = short_deadline_sdk
    plane = cancelled_during_a_write(module, monkeypatch)
    module.stub_session.plane = module.data_plane = restored = ReplayPlane(lambda _p: False)
    restored.replay = written_before(plane, missing)
    second = RecordingModel(says("Welcome back."))

    events = run_turn(module, gateway(cedar), second)

    assert events[-1]["type"] == "result", why
    assert_usable(second.requests[0])


class RestoreFails(DataPlane):
    """A data plane that fails the SDK's ``list_messages`` read, the one that restores history.

    It is the read with no metadata filter for the traveler's own actor, apart from the
    Runtime's own Memory span (``maxResults`` 50). The SDK swallows its failure.
    """

    failing = False

    def list_events(self, **params):
        history_read = "filter" not in params and params["maxResults"] != 50
        if self.failing and history_read and not params["actorId"].startswith("session_"):
            self.calls.append(("list_events", params))
            raise RuntimeError("ListEvents failed for arn:aws:bedrock-agentcore:us-east-1:"
                               "111122223333:memory/mem-0123456789")
        return super().list_events(**params)


class ReplayRestoreFails(RestoreFails, ReplayPlane):
    """A data plane that lists back what was written, and fails the history read on demand."""


@needs_sdk
def test_a_failed_memory_restore_fails_the_memory_span_and_the_turn_continues(
        sdk_runtime, caplog):
    module = sdk_runtime
    module.data_plane = module.stub_session.plane = plane = ReplayRestoreFails(lambda _: False)
    module.memory_span = module.real_memory_span
    run_turn(module, gateway(cedar), ScriptedModel(says("Welcome back.")))
    plane.failing = True

    with caplog.at_level("WARNING"):
        events = run_turn(module, gateway(cedar), ScriptedModel(says("Welcome back again.")))

    [span] = [event for event in events if event["type"] == "activity"
              and event["title"].startswith("AgentCore Memory")]
    assert span["title"] == "AgentCore Memory: conversation history could not be restored"
    assert span["activity_type"] == "error" and span["telemetry"]["status"] == "error"
    assert "111122223333" not in json.dumps(span) and "ListEvents" not in json.dumps(span)
    assert events[-1]["type"] == "result" and "Welcome back again." in events[-1]["message"]
    warned = [record.getMessage() for record in caplog.records
              if "could not be restored" in record.getMessage()]
    assert len(warned) == 1 and "111122223333" not in warned[0]
    assert CONVERSATION in warned[0]


@pytest.fixture
def busy_worker(runtime, monkeypatch):
    """The Runtime with one off-loop worker, held busy until the test sets ``gate``."""
    executor, gate = turn_work.OffLoopExecutor(1), threading.Event()
    monkeypatch.setattr(runtime, "OFFLOOP", executor)
    monkeypatch.setattr(runtime, "REPAIRS", turn_work.HistoryRepairs(executor))
    monkeypatch.setattr(runtime, "SETTLE_SECONDS", 0.1)
    monkeypatch.setattr(runtime, "CLOSE_SECONDS", 0.1)
    executor.submit(gate.wait, 5)
    try:
        yield SimpleNamespace(runtime=runtime, executor=executor, gate=gate)
    finally:
        gate.set()
        until(lambda: executor.busy == 0)


def test_a_queued_history_repair_survives_the_wait_and_runs_once_a_worker_frees(busy_worker):
    runtime = busy_worker.runtime
    session, agent = GatedSession(), open_history()
    session.gate.set()

    asyncio.run(runtime.close_history(
        history_turn(), agent, session, SimpleNamespace(done=lambda: True)))

    assert runtime.REPAIRS.running("conv-9"), "the queued repair was dropped from the registry"
    busy_worker.gate.set()
    until(lambda: not runtime.REPAIRS.running("conv-9"))
    assert [message["role"] for message in session.appended] == ["user", "assistant"]
    assert asyncio.run(runtime.REPAIRS.finish("conv-9", 0.1)) is True


def test_a_queued_history_repair_is_reported_to_the_next_turn_while_it_waits(busy_worker):
    runtime = busy_worker.runtime
    session, agent = GatedSession(), open_history()
    session.gate.set()

    asyncio.run(runtime.close_history(
        history_turn(), agent, session, SimpleNamespace(done=lambda: True)))

    assert asyncio.run(runtime.REPAIRS.finish("conv-9", 0.1)) is False


def test_a_queued_gateway_close_still_runs_once_a_worker_frees(busy_worker):
    closed = []
    client = type("Client", (), {"__exit__": lambda self, *exc: closed.append(exc)})()

    asyncio.run(busy_worker.runtime.close_gateway(client, (None, None, None)))

    assert closed == []
    busy_worker.gate.set()
    until(lambda: closed)
    assert closed == [(None, None, None)]


async def test_a_cancelled_wait_for_a_job_that_later_fails_leaves_no_unretrieved_error():
    import gc
    from concurrent.futures import Future

    reports = []
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(lambda _loop, context: reports.append(context))
    job = Future()
    wait = asyncio.create_task(turn_work.wait_for_job(job, 30))
    await asyncio.sleep(0.05)
    wait.cancel()
    with pytest.raises(asyncio.CancelledError):
        await wait
    job.set_exception(RuntimeError("late failure"))
    await asyncio.sleep(0.05)
    del wait, job
    gc.collect()
    await asyncio.sleep(0.05)

    assert reports == []
