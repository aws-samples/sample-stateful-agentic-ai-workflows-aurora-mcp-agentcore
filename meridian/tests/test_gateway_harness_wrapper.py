"""The harness's Lambda wrapper adds only test switches around the production interceptor."""

import importlib.util
import json
import sys
from types import SimpleNamespace

import pytest

from scripts.gateway_harness import sources
from tests.gateway_interceptor_support import load_interceptor
from tests.test_gateway_interceptor import DECOY, JORDAN, load, token_for


@pytest.fixture
def wrapper(monkeypatch):
    """The wrapper module, importing the production file the way the harness package does."""
    monkeypatch.setitem(sys.modules, "lambda_function", load_interceptor())
    monkeypatch.setenv("PINNED_TOOLS", "MeridianHolds___create_courtesy_hold")
    path = sources.HARNESS_LAMBDAS / "interceptor_wrapper.py"
    spec = importlib.util.spec_from_file_location("harness_interceptor_wrapper", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def arguments_of(output):
    return output["mcp"]["transformedGatewayRequest"]["body"]["params"]["arguments"]


def test_pin_mode_is_the_production_interceptor_unchanged(wrapper, monkeypatch):
    monkeypatch.setenv("HARNESS_MODE", "pin")
    event = load("documented_tools_call_hold", token_for(traveler_id=DECOY))
    assert wrapper.lambda_handler(event, None) == load_interceptor().lambda_handler(event, None)


def test_extra_argument_mode_adds_a_property_the_schema_forbids(wrapper, monkeypatch):
    monkeypatch.setenv("HARNESS_MODE", "extra_argument")
    event = load("documented_tools_call_hold", token_for(traveler_id=DECOY))
    arguments = arguments_of(wrapper.lambda_handler(event, None))
    assert arguments["travelerId"] == DECOY and arguments["unexpectedField"]


def test_extra_argument_mode_leaves_a_refusal_alone(wrapper, monkeypatch):
    monkeypatch.setenv("HARNESS_MODE", "extra_argument")
    output = wrapper.lambda_handler(load("documented_tools_call_hold", token_for()), None)
    assert "transformedGatewayResponse" in output["mcp"]


def test_refuse_mode_answers_with_the_production_refusal(wrapper, monkeypatch):
    monkeypatch.setenv("HARNESS_MODE", "refuse")
    event = load("documented_tools_call_hold", token_for(traveler_id=JORDAN))
    assert wrapper.lambda_handler(event, None) == load_interceptor().refusal(7, "claim")


def test_refuse_mode_leaves_other_methods_alone(wrapper, monkeypatch):
    monkeypatch.setenv("HARNESS_MODE", "refuse")
    event = load("documented_tools_list", token_for(traveler_id=JORDAN))
    assert wrapper.lambda_handler(event, None) == load_interceptor().lambda_handler(event, None)


def test_recording_logs_the_event_with_the_token_replaced(wrapper, monkeypatch, capsys):
    monkeypatch.setenv("HARNESS_RECORD", "1")
    token = token_for(traveler_id=DECOY)
    wrapper.lambda_handler(load("documented_tools_call_hold", token), None)
    logged = capsys.readouterr().out
    assert token not in logged and token.split(".")[1] not in logged
    event = json.loads(logged.split(wrapper.RECORD_MARKER, 1)[1])
    assert event["mcp"]["gatewayRequest"]["headers"]["Authorization"] == "Bearer {{TOKEN}}"
    assert event["mcp"]["gatewayRequest"]["body"]["method"] == "tools/call"


def test_without_the_record_switch_nothing_is_logged(wrapper, capsys):
    wrapper.lambda_handler(load("documented_tools_call_hold"), None)
    assert capsys.readouterr().out == ""


def test_the_echo_target_returns_its_event_and_the_gateway_context():
    spec = importlib.util.spec_from_file_location(
        "harness_echo_target", sources.HARNESS_LAMBDAS / "echo_target.py")
    echo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(echo)
    context = SimpleNamespace(client_context=SimpleNamespace(
        custom={"bedrockAgentCoreToolName": "EchoTarget___echo", "n": 3}))
    assert echo.lambda_handler({"travelerId": "t"}, context) == {
        "event": {"travelerId": "t"},
        "custom": {"bedrockAgentCoreToolName": "EchoTarget___echo", "n": "3"}}
    assert echo.lambda_handler({}, SimpleNamespace(client_context=None))["custom"] == {}
