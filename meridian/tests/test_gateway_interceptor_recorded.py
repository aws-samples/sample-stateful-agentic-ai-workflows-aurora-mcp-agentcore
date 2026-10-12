"""Events recorded from the live throwaway Gateway run, fed to the real interceptor handler.

The fixtures are the three distinct events the Gateway sent to the harness's interceptor, with the
token, host, caller address, trace id and account replaced by obvious fakes. They show the request
shape the live Gateway sends and that the handler's output shape is the one the Gateway accepted.
"""

from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path

import pytest

from tests.gateway_interceptor_support import load_interceptor

FIXTURES = Path(__file__).parent / "fixtures" / "gateway_interceptor"
NAMES = ("recorded_1", "recorded_2", "recorded_3")
ECHO = "EchoTarget___echo"
JORDAN = "trv_meridian_demo"
DECOY = "trv_demo_decoy"
FAKE_HOST = "gw-test.gateway.bedrock-agentcore.us-east-1.amazonaws.com"
FAKE_ACCOUNT = "123456789012"
FAKE_IP = "192.0.2.1"
interceptor = load_interceptor()


def segment(value: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()


def token_for(**claims) -> str:
    payload = {"token_use": "access", "exp": int(time.time()) + 3600, **claims}
    return f"{segment({'alg': 'RS256'})}.{segment(payload)}.signature"


def load(name: str, token: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text().replace("{{TOKEN}}", token))


@pytest.fixture(autouse=True)
def echo_is_pinned(monkeypatch):
    monkeypatch.setenv("PINNED_TOOLS", ECHO)


@pytest.mark.parametrize("name", NAMES)
def test_the_recorded_request_has_the_documented_shape(name):
    event = load(name, token_for(traveler_id=DECOY))
    assert event["interceptorInputVersion"] == "1.0"
    request = event["mcp"]["gatewayRequest"]
    assert request["path"] == "/mcp" and request["httpMethod"] == "POST"
    assert request["headers"]["Authorization"].startswith("Bearer ")
    assert request["body"]["jsonrpc"] == "2.0"
    assert json.loads(event["mcp"]["rawGatewayRequest"]["body"]) == request["body"]


def test_the_recorded_tool_call_is_the_echo_tool_with_the_caller_named_traveler():
    body = load("recorded_1", token_for())["mcp"]["gatewayRequest"]["body"]
    assert body["method"] == "tools/call" and body["params"]["name"] == ECHO
    assert body["params"]["arguments"] == {"note": "control", "travelerId": JORDAN}


def test_a_decoy_token_gets_the_rewrite_shape_the_gateway_accepted():
    event = load("recorded_1", token_for(traveler_id=DECOY))
    output = interceptor.lambda_handler(event, None)
    assert output["interceptorOutputVersion"] == "1.0"
    assert set(output["mcp"]) == {"transformedGatewayRequest"}
    assert set(output["mcp"]["transformedGatewayRequest"]) == {"body"}
    body = output["mcp"]["transformedGatewayRequest"]["body"]
    assert body["params"]["arguments"] == {"note": "control", "travelerId": DECOY}
    assert (body["jsonrpc"], body["id"], body["method"]) == ("2.0", 1, "tools/call")
    assert body["params"]["name"] == ECHO


def test_the_travelers_own_token_leaves_the_recorded_call_unchanged():
    event = load("recorded_1", token_for(traveler_id=JORDAN))
    body = interceptor.lambda_handler(event, None)["mcp"]["transformedGatewayRequest"]["body"]
    assert body == event["mcp"]["gatewayRequest"]["body"]


def test_a_token_without_a_traveler_gets_the_refusal_shape_the_gateway_accepted():
    event = load("recorded_1", token_for())
    output = interceptor.lambda_handler(event, None)
    assert output["interceptorOutputVersion"] == "1.0"
    assert set(output["mcp"]) == {"transformedGatewayResponse"}
    response = output["mcp"]["transformedGatewayResponse"]
    assert set(response) == {"statusCode", "body"} and response["statusCode"] == 200
    assert response["body"]["jsonrpc"] == "2.0" and response["body"]["id"] == 1
    result = response["body"]["result"]
    assert set(result) == {"isError", "content"} and result["isError"] is True
    assert result["content"] == [{"type": "text", "text": (
        "Identity Check Failed: the access token carries no single traveler for this system.")}]


@pytest.mark.parametrize("name", ["recorded_2", "recorded_3"])
def test_initialize_and_tools_list_pass_through_unchanged(name):
    event = load(name, token_for())
    output = interceptor.lambda_handler(event, None)
    assert set(output["mcp"]) == {"transformedGatewayRequest"}
    assert output["mcp"]["transformedGatewayRequest"]["body"] == (
        event["mcp"]["gatewayRequest"]["body"])


def test_the_fixtures_hold_no_token_real_host_address_or_account():
    for name in NAMES:
        text = (FIXTURES / f"{name}.json").read_text()
        assert "eyJ" not in text and "{{TOKEN}}" in text
        assert set(re.findall(r"\b[0-9]{12}\b", text)) <= {FAKE_ACCOUNT}
        assert set(re.findall(r"[\w.-]+\.amazonaws\.com", text)) <= {FAKE_HOST}
        assert set(re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)) <= {FAKE_IP}
        assert "throwaway" not in text and "arn:aws" not in text
        assert not re.search(r"Root=1-[0-9a-f]{8}-(?!0{24})", text)
