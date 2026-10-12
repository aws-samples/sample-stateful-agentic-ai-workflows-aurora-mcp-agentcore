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


def test_bad_type_mode_rewrites_the_pinned_traveler_to_an_integer(wrapper, monkeypatch):
    monkeypatch.setenv("HARNESS_MODE", "bad_type")
    event = load("documented_tools_call_hold", token_for(traveler_id=DECOY))
    arguments = arguments_of(wrapper.lambda_handler(event, None))
    traveler = arguments["travelerId"]
    assert isinstance(traveler, int) and not isinstance(traveler, bool)


def test_drop_required_mode_removes_the_traveler_argument(wrapper, monkeypatch):
    monkeypatch.setenv("HARNESS_MODE", "drop_required")
    event = load("documented_tools_call_hold", token_for(traveler_id=DECOY))
    pinned = arguments_of(load_interceptor().lambda_handler(event, None))
    arguments = arguments_of(wrapper.lambda_handler(event, None))
    assert "travelerId" not in arguments
    assert arguments == {k: v for k, v in pinned.items() if k != "travelerId"}


@pytest.mark.parametrize("mode", ["bad_type", "drop_required"])
def test_rewrite_modes_leave_a_refusal_alone(wrapper, monkeypatch, mode):
    monkeypatch.setenv("HARNESS_MODE", mode)
    output = wrapper.lambda_handler(load("documented_tools_call_hold", token_for()), None)
    assert "transformedGatewayResponse" in output["mcp"]


@pytest.mark.parametrize("mode", ["bad_type", "drop_required"])
def test_rewrite_modes_leave_other_methods_alone(wrapper, monkeypatch, mode):
    monkeypatch.setenv("HARNESS_MODE", mode)
    event = load("documented_tools_list", token_for(traveler_id=JORDAN))
    assert wrapper.lambda_handler(event, None) == load_interceptor().lambda_handler(event, None)


def test_an_unknown_mode_is_an_error_not_a_silent_pin(wrapper, monkeypatch):
    monkeypatch.setenv("HARNESS_MODE", "extra_argument")
    with pytest.raises(ValueError, match="HARNESS_MODE"):
        wrapper.lambda_handler(load("documented_tools_call_hold", token_for()), None)


def test_pin_mode_with_a_malformed_event_equals_the_production_output(wrapper, monkeypatch):
    monkeypatch.setenv("HARNESS_MODE", "pin")
    malformed = {"interceptorInputVersion": "1.0", "mcp": {"gatewayRequest": 3}}
    production = load_interceptor()
    for event in ({}, {"mcp": {}}, malformed):
        assert wrapper.lambda_handler(event, None) == production.lambda_handler(event, None)


def test_refuse_mode_with_a_malformed_event_falls_through_to_production(wrapper, monkeypatch):
    monkeypatch.setenv("HARNESS_MODE", "refuse")
    assert wrapper.lambda_handler({}, None) == load_interceptor().lambda_handler({}, None)


def test_refuse_mode_answers_with_the_production_refusal(wrapper, monkeypatch):
    monkeypatch.setenv("HARNESS_MODE", "refuse")
    event = load("documented_tools_call_hold", token_for(traveler_id=JORDAN))
    assert wrapper.lambda_handler(event, None) == load_interceptor().refusal(7, "claim")


def test_off_mode_forwards_the_callers_own_arguments_untouched(wrapper, monkeypatch):
    monkeypatch.setenv("HARNESS_MODE", "off")
    event = load("documented_tools_call_hold", token_for(traveler_id=DECOY))
    body = event["mcp"]["gatewayRequest"]["body"]
    output = wrapper.lambda_handler(event, None)
    assert output == {
        "interceptorOutputVersion": "1.0",
        "mcp": {"transformedGatewayRequest": {"body": body}},
    }
    assert arguments_of(output)["travelerId"] == body["params"]["arguments"]["travelerId"]


def test_off_mode_does_not_decode_the_token(wrapper, monkeypatch):
    monkeypatch.setenv("HARNESS_MODE", "off")
    event = load("documented_tools_call_hold", "not-a-token")
    assert "transformedGatewayRequest" in wrapper.lambda_handler(event, None)["mcp"]


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


def recorded(wrapper, monkeypatch, capsys, event):
    monkeypatch.setenv("HARNESS_RECORD", "1")
    wrapper.lambda_handler(event, None)
    return capsys.readouterr().out


def test_a_token_in_a_non_standard_header_is_masked_in_the_log(wrapper, monkeypatch, capsys):
    token = token_for(traveler_id=DECOY)
    event = load("documented_tools_call_hold", token)
    event["mcp"]["gatewayRequest"]["headers"]["X-Forwarded-Auth"] = f"Bearer {token}"
    event["mcp"]["gatewayRequest"]["headers"]["X-Raw"] = token
    logged = recorded(wrapper, monkeypatch, capsys, event)
    assert token not in logged and token.split(".")[1] not in logged
    assert json.loads(logged.split(wrapper.RECORD_MARKER, 1)[1])


def test_a_token_in_the_body_is_masked_in_the_log(wrapper, monkeypatch, capsys):
    token = token_for(traveler_id=DECOY)
    event = load("documented_tools_call_hold", token)
    event["mcp"]["gatewayRequest"]["body"]["params"]["arguments"]["note"] = (
        f"use Bearer {token} please")
    logged = recorded(wrapper, monkeypatch, capsys, event)
    assert token not in logged and token.split(".")[1] not in logged


def test_a_lowercase_bearer_header_is_masked_in_the_log(wrapper, monkeypatch, capsys):
    secret = "opaque-secret-value-123"
    event = load("documented_tools_call_hold", token_for(traveler_id=DECOY))
    event["mcp"]["gatewayRequest"]["headers"]["X-Other"] = f"bearer   {secret}"
    logged = recorded(wrapper, monkeypatch, capsys, event)
    assert secret not in logged
    headers = json.loads(logged.split(wrapper.RECORD_MARKER, 1)[1])["mcp"]["gatewayRequest"][
        "headers"]
    assert headers["X-Other"] == "Bearer {{TOKEN}}"
