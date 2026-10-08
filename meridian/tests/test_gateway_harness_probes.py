"""The probes send real-shaped MCP calls with bearer tokens and read every reply shape."""

import json

import httpx
import pytest
from botocore.exceptions import ClientError

from scripts.gateway_harness import probes
from scripts.gateway_harness.verdicts import DECOY, JORDAN


def echo_reply(traveler):
    text = json.dumps({"event": {"travelerId": traveler}, "custom": {}})
    return {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": text}]}}


def mcp(handler):
    return probes.McpHttp("https://gw.example/mcp", transport=httpx.MockTransport(handler))


def test_a_call_posts_the_tool_call_with_the_bearer_and_never_the_token_in_the_body():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=echo_reply(JORDAN))

    outcome = mcp(handler).call_tool("tok-123", {"travelerId": JORDAN})
    request = seen[0]
    assert request.headers["authorization"] == "Bearer tok-123"
    body = json.loads(request.content)
    assert body["method"] == "tools/call"
    assert body["params"] == {"name": "EchoTarget___echo", "arguments": {"travelerId": JORDAN}}
    assert "tok-123" not in request.content.decode()
    assert outcome.kind == "ok"


def test_an_event_stream_reply_is_read():
    text = "event: message\ndata: " + json.dumps(echo_reply(DECOY)) + "\n\n"
    reply = httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
    assert mcp(lambda request: reply).call_tool("t", {}).kind == "ok"


def test_a_non_json_error_page_is_an_http_error():
    reply = httpx.Response(502, text="<html>bad gateway</html>")
    outcome = mcp(lambda request: reply).call_tool("t", {})
    assert (outcome.kind, outcome.status) == ("http_error", 502)


def test_a_transport_failure_is_an_http_error_that_names_no_url_or_token():
    def handler(request):
        raise httpx.ConnectError("could not reach https://gw.example/mcp with Bearer tok-9")

    outcome = mcp(handler).call_tool("tok-9", {})
    assert (outcome.kind, outcome.status) == ("http_error", 0)
    assert "tok-9" not in outcome.message and "gw.example" not in outcome.message
    assert "ConnectError" in outcome.message


class FakeGateway:
    def __init__(self):
        self.modes = []
        self.live = type("Live", (), {"binding_policy_accepted": True})()

    def set_interceptor_mode(self, mode):
        self.modes.append(mode)


def test_the_probes_are_the_q2_redesign_and_none_uses_additional_properties():
    assert [p.name for p in probes.PROBES] == [
        "jordan_names_jordan", "decoy_names_jordan", "omitted_required", "bad_type",
        "drop_required", "forced_refusal", "cedar_alone"]
    assert [p.mode for p in probes.PROBES] == [
        "pin", "pin", "pin", "bad_type", "drop_required", "refuse", "off"]
    assert "travelerId" not in probes.PROBES[2].arguments
    assert not any("extra" in p.name for p in probes.PROBES)


def test_probes_run_in_mode_groups_with_each_users_token():
    calls = []

    def handler(request):
        body = json.loads(request.content)
        if body["method"] == "tools/list":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"tools": [
                {"name": "EchoTarget___echo"}]}})
        if body["method"] != "tools/call":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})
        arguments = body["params"]["arguments"]
        calls.append((request.headers["authorization"], arguments))
        return httpx.Response(200, json=echo_reply(arguments.get("travelerId", DECOY)))

    gateway = FakeGateway()
    obs = probes.run_probes(gateway, mcp(handler), {"jordan": "J", "decoy": "D"},
                            sleep=lambda s: None)
    assert gateway.modes == ["bad_type", "drop_required", "refuse", "off"]
    assert [auth for auth, _ in calls] == ["Bearer J"] + ["Bearer D"] * 6
    assert calls[-1][1]["travelerId"] == JORDAN, "the Cedar-alone probe names Jordan"
    assert obs.listed_tools == ["EchoTarget___echo"]
    assert set(obs.outcomes) == {p.name for p in probes.PROBES}
    assert obs.binding_policy_accepted is True


def test_the_control_probe_is_retried_while_the_gateway_settles():
    replies = iter([httpx.Response(403, json={"message": "no"}),
                    httpx.Response(200, json={"error": {"code": -32002, "message": "wait"}}),
                    httpx.Response(200, json=echo_reply(JORDAN))])
    sleeps = []

    def handler(request):
        try:
            return next(replies)
        except StopIteration:
            return httpx.Response(200, json=echo_reply(DECOY))

    obs = probes.run_probes(FakeGateway(), mcp(handler), {"jordan": "J", "decoy": "D"},
                            sleep=sleeps.append)
    assert obs.outcomes["jordan_names_jordan"].kind == "ok"
    assert sleeps == [probes.CONTROL_INTERVAL] * 2


def test_the_control_probe_gives_up_after_its_attempts_and_reports_the_last_outcome():
    sleeps = []
    obs = probes.run_probes(
        FakeGateway(), mcp(lambda request: httpx.Response(401, json={"message": "no"})),
        {"jordan": "J", "decoy": "D"}, sleep=sleeps.append)
    assert obs.outcomes["jordan_names_jordan"].kind == "http_error"
    assert len(sleeps) == probes.CONTROL_ATTEMPTS - 1


class FakeLogs:
    def __init__(self, pages_by_attempt):
        self.attempts = iter(pages_by_attempt)

    def get_paginator(self, name):
        pages = next(self.attempts)
        return type("P", (), {"paginate": lambda self_, **kw: pages})()


def logged(method, tool=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if tool:
        body["params"] = {"name": tool, "arguments": {}}
    event = {"mcp": {"gatewayRequest": {"body": body, "headers": {
        "Authorization": "Bearer {{TOKEN}}"}}}}
    return {"message": "RECORDED_EVENT " + json.dumps(event)}


def test_recorded_events_are_deduplicated_and_collected_across_polls():
    logs = FakeLogs([
        [{"events": [logged("tools/call", "EchoTarget___echo"),
                     logged("tools/call", "EchoTarget___echo"),
                     {"message": "START RequestId: x"}]}],
        [{"events": [logged("tools/list"), logged("tools/call", "Other___tool")]}],
    ])
    events = probes.recorded_events(logs, "fn", sleep=lambda s: None)
    methods = sorted(e["mcp"]["gatewayRequest"]["body"]["method"] for e in events)
    assert methods == ["tools/call", "tools/call", "tools/list"]
    assert all("Bearer {{TOKEN}}" in json.dumps(e) for e in events)


def test_a_malformed_log_line_is_skipped_not_fatal():
    broken = {"message": "RECORDED_EVENT {not json}"}
    shapeless = {"message": 'RECORDED_EVENT {"mcp": {}}'}
    logs = FakeLogs([[{"events": [broken, shapeless, logged("tools/list")]}]])
    events = probes.recorded_events(logs, "fn", wanted=1, sleep=lambda s: None)
    assert len(events) == 1


def test_a_missing_log_group_is_polled_again_not_fatal():
    def missing():
        raise ClientError({"Error": {"Code": "ResourceNotFoundException", "Message": "x"}},
                          "FilterLogEvents")

    class Logs:
        def get_paginator(self, name):
            return type("P", (), {"paginate": lambda self_, **kw: missing()})()

    assert probes.recorded_events(Logs(), "fn", attempts=2, sleep=lambda s: None) == []


def test_another_log_error_is_raised():
    class Logs:
        def get_paginator(self, name):
            def boom(**kw):
                raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "x"}},
                                  "FilterLogEvents")
            return type("P", (), {"paginate": lambda self_, **kw: boom()})()

    with pytest.raises(ClientError):
        probes.recorded_events(Logs(), "fn", attempts=1, sleep=lambda s: None)


def test_listing_tools_sends_initialize_then_tools_list_and_survives_failure():
    methods = []

    def handler(request):
        methods.append(json.loads(request.content)["method"])
        return httpx.Response(401, json={"message": "no"})

    assert mcp(handler).list_tools("J") == []
    assert methods == ["initialize", "tools/list"]


def test_a_url_httpx_rejects_is_a_transport_error_not_a_crash():
    def invalid(request):
        raise httpx.InvalidURL("Invalid URL")

    http = mcp(invalid)
    outcome = http.call_tool("t", {})
    assert (outcome.kind, outcome.status) == ("http_error", 0)
    assert outcome.message == "transport failure: InvalidURL"
    assert http.list_tools("t") == []
