"""MCP tool errors reach the model as one stable answer, and only a suspect client resets.

The error strings are built by the installed FastMCP and Strands, over a real stdio subprocess
(tests/fixtures/phase3_stdio_server.py; no network, no AWS), so an upgrade that rewords either
one fails here.
"""

import asyncio
import contextlib
import json
import logging
import os
import re
import signal
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from strands.tools.mcp import MCPClient
from strands.tools.mcp.mcp_client import CLIENT_SESSION_NOT_RUNNING_ERROR_MESSAGE

from backend.agents.phase_03_retrieval import agent as phase3
from backend.agents import strands_hooks
from backend.agents.phase_03_retrieval import hooks, mcp_session
from backend.agents.phase_03_retrieval import tools as phase3_tools
from tests.test_phase3_agent import RecordingModel, tool_results, tool_turn

SERVER = Path(__file__).parent / "fixtures" / "phase3_stdio_server.py"
LEAK_TEXT = ("hunter2", "123456789012", "Error executing tool")


def server_parameters(mode="catalog"):
    return StdioServerParameters(command=sys.executable, args=[str(SERVER), mode])


def fixture_client():
    return MCPClient(lambda: stdio_client(server_parameters()), startup_timeout=20)


@contextmanager
def started_client():
    client = fixture_client()
    client.start()
    try:
        yield client
    finally:
        client.stop(None, None, None)


def call(client, name, **arguments):
    return client.call_tool_sync(tool_use_id="t1", name=name, arguments=arguments)


def test_a_server_side_tool_failure_has_the_text_the_hook_calls_server_reported():
    with started_client() as client:
        raised = call(client, "price_range", destination="Kyoto")
        invalid = call(client, "currency_convert", amount="abc", from_ccy="USD", to_ccy="EUR")

    for result in (raised, invalid):
        assert result["status"] == "error"
        assert hooks.error_origin(result) == "server"
    assert raised["content"][0]["text"].startswith("Error executing tool price_range: ")


def test_a_client_side_failure_has_the_text_the_hook_calls_client_built():
    with started_client() as client:
        died = call(client, "region_inventory", region="Asia")
        after = call(client, "price_range", destination="Kyoto")

    for result in (died, after):
        assert result["status"] == "error"
        assert hooks.error_origin(result) == "client"
    assert died["content"][0]["text"].startswith("Tool execution failed: ")


@pytest.mark.parametrize("result, origin", [
    ({"status": "error", "content": [{"text": "Tool execution cancelled locally; more"}]},
     "client"),
    ({"status": "error", "content": [{"text": "Error: wedged pipe"}]}, "unknown"),
    ({"status": "error", "content": [{"text": "MCP Elicitation required: [x]"}]}, "unknown"),
    ({"status": "error", "content": []}, "unknown"),
    ({"status": "error"}, "unknown"),
])
def test_other_error_shapes_are_read_as_client_or_unknown(result, origin):
    assert hooks.error_origin(result) == origin


class RecordingPool(mcp_session.ConciergeMcpPool):
    """The real pool over the fixture server, recording how each turn gave its client back."""

    def __init__(self):
        super().__init__(lambda: fixture_client())
        self.given_back = []

    def release(self, generation):
        self.given_back.append("release")
        super().release(generation)

    def reset(self, generation):
        self.given_back.append("reset")
        super().reset(generation)


def run_on_real_server(name, arguments, final="Done."):
    pool, model = RecordingPool(), RecordingModel([tool_turn(name, arguments),
                                                   {"kind": "text", "text": final}])
    try:
        turn = asyncio.run(phase3.run_retrieval_turn(
            "Prices please", traveler_id="trv_pinned", model=model, pool=pool))
    finally:
        pool.close()
    return turn, model, pool


def test_a_server_reported_error_reaches_the_model_only_as_the_stable_answer():
    turn, model, pool = run_on_real_server("price_range", {"destination": "Kyoto"})

    [result] = tool_results(model.messages[1])
    assert json.loads(result["content"][0]["text"]) == {
        "error": "catalog_tool_failed", "tool": "price_range"}
    sent = json.dumps(model.messages)
    assert not [leak for leak in LEAK_TEXT if leak in sent]
    assert "Tool call failed: price_range" in [a.title for a in turn.activities]
    assert pool.given_back == ["release"], "the server answered, so its session is alive"


def test_a_server_reported_error_reaches_the_log_without_its_credential(caplog):
    with caplog.at_level(logging.WARNING, logger=hooks.__name__):
        run_on_real_server("price_range", {"destination": "Kyoto"})

    assert "password=<masked>" in caplog.text and "<acct>" in caplog.text
    assert "hunter2" not in caplog.text and "123456789012" not in caplog.text


def test_a_client_built_error_reaches_the_model_as_the_stable_answer_and_resets():
    turn, model, pool = run_on_real_server("region_inventory", {"region": "Asia"})

    [result] = tool_results(model.messages[1])
    assert json.loads(result["content"][0]["text"]) == {
        "error": "catalog_tool_failed", "tool": "region_inventory"}
    assert "Connection closed" not in json.dumps(model.messages)
    assert pool.given_back == ["reset"], "the server process is gone"


def test_a_validation_error_gets_the_same_answer_and_keeps_the_client():
    _, model, pool = run_on_real_server(
        "currency_convert", {"amount": "abc", "from_ccy": "USD", "to_ccy": "EUR"})

    [result] = tool_results(model.messages[1])
    assert json.loads(result["content"][0]["text"])["error"] == "catalog_tool_failed"
    assert "validation" not in json.dumps(model.messages)
    assert pool.given_back == ["release"]


def test_an_unsupported_currency_is_an_answer_not_a_failure_over_real_mcp():
    turn, model, pool = run_on_real_server(
        "currency_convert", {"amount": 1, "from_ccy": "USD", "to_ccy": "XXX"})

    [result] = tool_results(model.messages[1])
    assert json.loads(result["content"][0]["text"])["error"] == "unsupported_currency"
    assert not [a for a in turn.activities if a.title.startswith("Tool call failed")]
    assert pool.given_back == ["release"]


def test_the_hook_keeps_where_each_failure_came_from_and_a_masked_detail():
    trace_hooks = hooks.RetrievalTraceHooks([])
    event = SimpleNamespace(
        tool_use={"name": "price_range", "toolUseId": "t9", "input": {}}, exception=None,
        result={"status": "error", "toolUseId": "t9", "content": [{
            "text": "Error executing tool price_range: boom in 123456789012"}]})

    trace_hooks.after(event)

    [failure] = trace_hooks.mcp_failures
    assert (failure.tool, failure.origin) == ("price_range", "server")
    assert failure.detail == "Error executing tool price_range: boom in <acct>"
    assert not trace_hooks.client_suspect


START_TIMEOUT = 2
CLEANUP_SLACK = 6


def test_an_abandoned_start_leaves_no_server_process_and_no_threads(tmp_path):
    """A server that never answers ``initialize`` is gone once its start times out."""
    pid_file = tmp_path / "server.pid"
    parameters = StdioServerParameters(
        command=sys.executable, args=[str(SERVER), "silent", str(pid_file)])
    pool = mcp_session.ConciergeMcpPool(
        lambda: mcp_session.new_client(parameters, start_timeout=START_TIMEOUT),
        start_timeout=START_TIMEOUT)
    before = set(threading.enumerate())
    began = time.monotonic()

    with pytest.raises(TimeoutError):
        pool.acquire()

    assert pid_file.exists(), "the silent server never wrote its pid file, so it never started"
    child = int(pid_file.read_text())
    try:
        for worker in set(threading.enumerate()) - before:
            worker.join(max(0.0, began + START_TIMEOUT + CLEANUP_SLACK - time.monotonic()))
        assert not [t.name for t in set(threading.enumerate()) - before if t.is_alive()]
        with pytest.raises(ProcessLookupError):
            os.kill(child, 0)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(child, signal.SIGKILL)
        pool.close()


# ----------------------------------------------------- a setup failure over MCP ends the turn

SETUP_STOP = "A tool this turn needs could not be set up"


def run_loyalty(program):
    pool = RecordingPool()
    model = RecordingModel([
        tool_turn("loyalty_balance", {"travelerId": "trv_pinned", "program": program}),
        {"kind": "text", "text": "I could not check that."}])
    try:
        with pytest.raises(phase3.RetrievalTurnError) as stopped:
            asyncio.run(phase3.run_retrieval_turn(
                "My points", traveler_id="trv_pinned", model=model, pool=pool))
    finally:
        pool.close()
    return stopped.value, model, pool


@pytest.mark.parametrize("code", ["AccessDeniedException", "ExpiredTokenException"])
def test_an_mcp_tool_that_could_not_be_set_up_ends_the_turn_and_resets_the_client(code):
    error, model, pool = run_loyalty(code)

    assert str(error).startswith(SETUP_STOP)
    assert len(model.messages) == 1, "the model is not called again after the failed tool"
    assert pool.given_back == ["reset"], "expired credentials need a fresh server process"
    cause = str(error.__cause__)
    assert cause.startswith("Tool loyalty_balance could not run")
    assert "123456789012" not in cause and "hunter2" not in cause


def test_an_mcp_throttling_error_still_reaches_the_model_and_the_turn_goes_on():
    turn, model, pool = run_on_real_server("loyalty_balance", {
        "travelerId": "trv_pinned", "program": "ThrottlingException"})

    [result] = tool_results(model.messages[1])
    assert json.loads(result["content"][0]["text"])["error"] == "catalog_tool_failed"
    assert turn.message == "Done." and len(model.messages) == 2
    assert pool.given_back == ["release"]


def test_a_client_built_error_from_a_dead_session_ends_the_turn():
    trace_hooks = hooks.RetrievalTraceHooks([])
    text = "Tool execution failed: " + CLIENT_SESSION_NOT_RUNNING_ERROR_MESSAGE
    event = SimpleNamespace(
        tool_use={"name": "price_range", "toolUseId": "t9", "input": {}}, exception=None,
        result={"status": "error", "toolUseId": "t9", "content": [{"text": text}]})

    trace_hooks.after(event)

    [failure] = trace_hooks.mcp_failures
    assert failure.setup and failure.origin == "client" and trace_hooks.client_suspect


# ------------------------------------------- one answer for a failed call, on every surface


def test_a_failed_local_call_and_a_failed_mcp_call_give_the_model_the_same_result(
        monkeypatch):
    async def failing(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(phase3_tools.package_tools, "get_package_details", failing)
    _, local_model, _ = run_on_real_server("get_package_details", {"packageId": "CTY-002"})
    _, mcp_model, _ = run_on_real_server("price_range", {"destination": "Kyoto"})

    [local] = tool_results(local_model.messages[1])
    [remote] = tool_results(mcp_model.messages[1])
    assert local["status"] == remote["status"] == "success"
    assert json.loads(local["content"][0]["text"])["error"] == "catalog_tool_failed"
    assert local["content"][0].keys() == remote["content"][0].keys()
    assert json.loads(remote["content"][0]["text"]) == {
        "error": "catalog_tool_failed", "tool": "price_range"}


def test_the_trace_still_marks_the_replaced_mcp_call_failed():
    turn, _, _ = run_on_real_server("price_range", {"destination": "Kyoto"})

    assert "Tool call failed: price_range" in [a.title for a in turn.activities]


# --------------------------------------------- a local tool's argument error is kept


def test_a_local_tools_argument_error_reaches_the_model_and_releases_the_client():
    turn, model, pool = run_on_real_server("get_package_details", {"packageId": 12})

    [result] = tool_results(model.messages[1])
    text = json.dumps(result)
    assert result["status"] == "error" and "packageId" in text
    assert not re.search(r"\d{12}", text) and "hunter2" not in text
    assert turn.message == "Done." and len(model.messages) == 2
    assert pool.given_back == ["release"]


BOTO = "An error occurred ({}) when calling the ExecuteStatement operation: {}"


@pytest.mark.parametrize("text, setup", [
    ("Error executing tool t: " + BOTO.format("AccessDeniedException", "no"), True),
    ("Tool execution failed: " + BOTO.format("ExpiredTokenException", "old"), True),
    ("Error executing tool t: " + BOTO.format("UnrecognizedClientException", "bad"), True),
    ("Error executing tool t: " + BOTO.format("ThrottlingException", "slow"), False),
    ("Error executing tool t: " + BOTO.format("ValidationException", "bad sql"), False),
    ("Error executing tool t: " + BOTO.format("ValidationException", "model not found"), True),
    ("Tool execution failed: " + CLIENT_SESSION_NOT_RUNNING_ERROR_MESSAGE, True),
    ("Tool execution failed: Connection closed", False),
    ("Error executing tool t: 1 validation error for tInputSchema", False),
    ("Error executing tool t: " + BOTO.format("ThrottlingException", "x") + " "
     + BOTO.format("AccessDeniedException", "y"), False),
    ("search " + BOTO.format("AccessDeniedException", "typed by the model"), False),
])
def test_an_mcp_error_text_is_setup_class_only_by_its_aws_code(text, setup):
    assert strands_hooks.is_setup_error_text(text) is setup


def test_a_reinvocation_after_reset_does_not_stop_on_a_stale_mcp_failure():
    def failure(tool):
        return SimpleNamespace(setup=True, tool=tool, origin="server", detail="denied")

    failures = [failure("loyalty_balance")]
    stop = strands_hooks.StopOnToolSetupError(failures)
    stop.reset(None)

    first = SimpleNamespace(end_turn=None)
    stop.end_turn(first)

    assert first.end_turn is None and stop.message is None
    failures.append(failure("price_range"))
    second = SimpleNamespace(end_turn=None)
    stop.end_turn(second)
    assert second.end_turn is not None and "price_range" in stop.message
    assert [each.tool for each in failures] == ["loyalty_balance", "price_range"]
