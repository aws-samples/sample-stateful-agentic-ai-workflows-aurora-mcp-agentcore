"""The release probe tells a missing policy from a working tool."""

import json

import pytest

from backend.agents.phase_05_workflow.governed_tools import GatewayToolError
from scripts.smoke_gateway_tools import classify_release_probe, main


def reply(text, is_error=False):
    return {"result": {"content": [{"type": "text", "text": text}], "isError": is_error}}


def test_a_hold_that_does_not_exist_proves_the_tool_ran():
    ok, message = classify_release_probe(reply(json.dumps({"error": "hold_not_found"})))
    assert ok and "no such hold" in message


def test_default_deny_means_the_policy_is_not_deployed_yet():
    denied = reply("AuthorizeActionException - Tool Execution Denied: denied by default", True)
    ok, message = classify_release_probe(denied)
    assert not ok and "meridian_release_governance" in message


@pytest.mark.parametrize("raw", [
    reply("Identity Check Failed: the request carries no bearer token.", True),
    reply(json.dumps({"error": "traveler_not_authorized"})),
    reply(json.dumps({"error": "booking_not_held"})),
    reply(json.dumps({"error": "release_failed"})),
    reply(json.dumps({"release": {"status": "released", "replayed": False}})),
    reply("not json"),
])
def test_anything_else_is_not_a_pass(raw):
    assert classify_release_probe(raw)[0] is False


def test_the_message_never_carries_text_the_gateway_sent():
    sent = "Identity Check Failed: arn:aws:iam::123456789012:role/x Bearer abc.def.ghi"
    ok, message = classify_release_probe(reply(sent, True))
    assert not ok
    assert "123456789012" not in message and "abc.def.ghi" not in message


SENT = "arn:aws:iam::123456789012:role/x Bearer abc.def.ghi password=hunter2"
LEAKS = ("123456789012", "arn:aws:iam::123456789012", "abc.def.ghi", "hunter2")
PACKAGE = {"package": {"package_id": "CTY-002", "name": "City break", "availability": 4}}


class FakeGateway:
    """A Gateway client whose answers or errors are set per tool."""

    gateway_url = "https://gateway.example.test/mcp"

    def __init__(self, details, release=None):
        self.details = details
        self.release = release if release is not None else reply(
            json.dumps({"error": "hold_not_found"}))

    def list_tools(self):
        return [{"name": "MeridianHolds___get_package_details"}], None

    def call_tool(self, name, arguments):
        answer = self.release if name.endswith("release_hold") else self.details
        if isinstance(answer, Exception):
            raise answer
        return answer


def run_main(gateway, capsys, caplog, *argv):
    with caplog.at_level("DEBUG"):
        code = main(list(argv), gateway=gateway)
    captured = capsys.readouterr()
    return code, captured.out + captured.err + caplog.text


def test_a_clean_run_prints_the_package_and_passes_the_probe(capsys, caplog):
    gateway = FakeGateway(reply(json.dumps(PACKAGE)))
    code, output = run_main(gateway, capsys, caplog, "CTY-002", "--probe-release")
    assert code == 0
    assert "ok: release_hold reached the Lambda" in output and "City break" in output


@pytest.mark.parametrize("details", [
    reply(f"AuthorizeActionException - Tool Execution Denied: {SENT}", True),
    reply(f"Identity Check Failed: {SENT}", True),
    {"error": {"code": -32000, "message": SENT}},
    reply(json.dumps({"error": SENT})),
    reply(SENT),
    RuntimeError(f"Gateway HTTP 403: {SENT}"),
    GatewayToolError("MeridianHolds___get_package_details", "the Gateway refused",
                     code="unauthorized"),
    OSError(SENT),
])
def test_a_failed_package_read_prints_no_gateway_text(details, capsys, caplog):
    code, output = run_main(FakeGateway(details), capsys, caplog)
    assert code == 1
    assert not [leak for leak in LEAKS if leak in output]


@pytest.mark.parametrize("release", [
    reply(f"Tool Execution Denied: {SENT}", True),
    reply(json.dumps({"error": SENT})),
    RuntimeError(f"Gateway HTTP 500: {SENT}"),
    GatewayToolError("MeridianHolds___release_hold", "the Gateway timed out", code="timeout"),
])
def test_a_failed_release_probe_prints_no_gateway_text(release, capsys, caplog):
    gateway = FakeGateway(reply(json.dumps(PACKAGE)), release)
    code, output = run_main(gateway, capsys, caplog, "--probe-release")
    assert code == 1
    assert not [leak for leak in LEAKS if leak in output]


def test_a_transport_failure_names_its_code(capsys, caplog):
    error = GatewayToolError("t", "the Gateway timed out", code="timeout")
    code, output = run_main(FakeGateway(error), capsys, caplog)
    assert code == 1 and "timeout" in output
