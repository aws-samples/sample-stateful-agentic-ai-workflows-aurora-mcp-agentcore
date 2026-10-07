"""The Gateway interceptor pins travelerId to the signed-in traveler, or refuses the call."""

from __future__ import annotations

import base64
import copy
import json
import logging
import re
import time
from pathlib import Path

import pytest

from tests.gateway_interceptor_support import load_interceptor

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "gateway_interceptor"
TARGET_DIR = ROOT / "meridian_agentcore" / "agentcore" / "gateway_targets"
TEMPLATE = ROOT / "meridian_agentcore" / "agentcore" / "agentcore.template.json"
interceptor = load_interceptor()

HOLD = "MeridianHolds___create_courtesy_hold"
JORDAN = "trv_meridian_demo"
DECOY = "trv_demo_decoy"


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def token_for(**claims) -> str:
    payload = {
        "token_use": "access",
        "client_id": "client-web",
        "sub": "sub-1",
        "iss": "https://issuer.example/pool",
        "exp": int(time.time()) + 3600,
        **claims,
    }
    header = b64(json.dumps({"alg": "RS256", "kid": "k1"}).encode())
    return f"{header}.{b64(json.dumps(payload).encode())}.signature"


def load(name: str, token: str | None = None) -> dict:
    text = (FIXTURES / f"{name}.json").read_text()
    return json.loads(text.replace("{{TOKEN}}", token or token_for(traveler_id=DECOY)))


def rewritten_body(output: dict) -> dict:
    assert output["interceptorOutputVersion"] == "1.0"
    assert set(output["mcp"]) == {"transformedGatewayRequest"}
    return output["mcp"]["transformedGatewayRequest"]["body"]


def refusal_text(output: dict) -> str:
    assert set(output["mcp"]) == {"transformedGatewayResponse"}
    response = output["mcp"]["transformedGatewayResponse"]
    assert response["statusCode"] == 200
    result = response["body"]["result"]
    assert result["isError"] is True
    return result["content"][0]["text"]


def test_the_models_traveler_is_replaced_by_the_tokens_claim():
    event = load("documented_tools_call_hold")
    body = rewritten_body(interceptor.lambda_handler(event, None))
    arguments = body["params"]["arguments"]
    assert arguments["travelerId"] == DECOY
    assert {k: v for k, v in arguments.items() if k != "travelerId"} == {
        k: v for k, v in event["mcp"]["gatewayRequest"]["body"]["params"]["arguments"].items()
        if k != "travelerId"
    }
    assert (body["jsonrpc"], body["id"], body["method"]) == ("2.0", 7, "tools/call")


def test_the_input_event_is_not_modified():
    event = load("documented_tools_call_hold")
    before = copy.deepcopy(event)
    interceptor.lambda_handler(event, None)
    assert event == before


def test_a_call_with_no_traveler_argument_gets_the_claim():
    event = load("documented_tools_call_hold")
    del event["mcp"]["gatewayRequest"]["body"]["params"]["arguments"]["travelerId"]
    body = rewritten_body(interceptor.lambda_handler(event, None))
    assert body["params"]["arguments"]["travelerId"] == DECOY


def test_a_matching_traveler_passes_through_unchanged():
    event = load("documented_tools_call_hold", token_for(traveler_id=JORDAN))
    body = rewritten_body(interceptor.lambda_handler(event, None))
    assert body == event["mcp"]["gatewayRequest"]["body"]


@pytest.mark.parametrize("name", ["documented_tools_call_search", "documented_tools_list"])
def test_tools_without_a_traveler_argument_pass_through_untouched(name):
    event = load(name)
    assert rewritten_body(interceptor.lambda_handler(event, None)) == (
        event["mcp"]["gatewayRequest"]["body"]
    )


def test_a_pass_through_does_not_need_a_traveler_claim():
    event = load("documented_tools_list", token_for())
    assert rewritten_body(interceptor.lambda_handler(event, None))["method"] == "tools/list"


@pytest.mark.parametrize("claims", [
    {},
    {"traveler_id": ""},
    {"traveler_id": ["trv_a", "trv_b"]},
    {"traveler_id": 7},
    {"traveler_id": "trv a;b"},
    {"traveler_id": "t" * 51},
])
def test_a_token_without_one_clear_traveler_is_refused_and_nothing_is_forwarded(claims):
    output = interceptor.lambda_handler(
        load("documented_tools_call_hold", token_for(**claims)), None
    )
    assert refusal_text(output) == (
        "Identity Check Failed: the access token carries no single traveler for this system."
    )
    assert output["mcp"]["transformedGatewayResponse"]["body"]["id"] == 7
    assert "transformedGatewayRequest" not in output["mcp"]


def test_an_id_token_is_refused_even_with_a_traveler_claim():
    event = load("documented_tools_call_hold", token_for(traveler_id=DECOY, token_use="id"))
    assert "not an access token" in refusal_text(interceptor.lambda_handler(event, None))


@pytest.mark.parametrize("token", ["", "abc", "a.b", "a.!!!.c", "é.é.é", "a." + "A" * 9000 + ".c"])
def test_a_token_that_is_not_a_jwt_is_refused(token):
    event = load("documented_tools_call_hold")
    event["mcp"]["gatewayRequest"]["headers"] = {"Authorization": f"Bearer {token}"}
    output = interceptor.lambda_handler(event, None)
    assert refusal_text(output).startswith("Identity Check Failed: ")


def test_a_missing_headers_block_is_refused_by_name():
    event = load("documented_tools_call_hold")
    del event["mcp"]["gatewayRequest"]["headers"]
    assert "did not pass the caller's headers" in refusal_text(
        interceptor.lambda_handler(event, None)
    )


@pytest.mark.parametrize("header", [
    {"Authorization": "Basic abc"}, {"X-Other": "Bearer abc"}, {}, {"Authorization": "  "},
])
def test_a_missing_or_wrong_scheme_authorization_header_is_refused(header):
    event = load("documented_tools_call_hold")
    event["mcp"]["gatewayRequest"]["headers"] = header
    assert "no bearer token" in refusal_text(interceptor.lambda_handler(event, None))


def test_the_header_and_scheme_are_matched_case_insensitively():
    event = load("documented_tools_call_hold")
    event["mcp"]["gatewayRequest"]["headers"] = {
        "authorization": "bEaReR " + token_for(traveler_id=DECOY)
    }
    assert rewritten_body(interceptor.lambda_handler(event, None))["params"]["arguments"][
        "travelerId"
    ] == DECOY


def test_a_bare_token_without_the_bearer_scheme_is_refused():
    event = load("documented_tools_call_hold")
    event["mcp"]["gatewayRequest"]["headers"] = {"authorization": token_for(traveler_id=DECOY)}
    output = interceptor.lambda_handler(event, None)
    assert "no bearer token" in refusal_text(output)
    assert "transformedGatewayRequest" not in output["mcp"]


def refused_with_headers(headers: dict) -> dict:
    event = load("documented_tools_call_hold")
    event["mcp"]["gatewayRequest"]["headers"] = headers
    output = interceptor.lambda_handler(event, None)
    assert "transformedGatewayRequest" not in output["mcp"]
    return output


def test_two_authorization_headers_differing_in_case_are_refused():
    output = refused_with_headers({
        "Authorization": "Bearer " + token_for(traveler_id=JORDAN),
        "authorization": "Bearer " + token_for(traveler_id=DECOY),
    })
    assert "more than one Authorization header" in refusal_text(output)


def test_two_identical_authorization_headers_are_refused_too():
    token = token_for(traveler_id=JORDAN)
    output = refused_with_headers({"Authorization": f"Bearer {token}", "AUTHORIZATION": token})
    assert "more than one Authorization header" in refusal_text(output)


def test_a_comma_joined_credential_is_refused():
    token = token_for(traveler_id=DECOY)
    refused_with_headers({"Authorization": f"Bearer {token}, x"})
    refused_with_headers({"Authorization": f"Bearer {token} x"})
    refused_with_headers({"Authorization": f"Bearer {token},"})


@pytest.mark.parametrize("mutate", [
    lambda t: t.replace(".", ".*", 1),
    lambda t: t.split(".")[0] + ".ab+/cd." + t.split(".")[2],
    lambda t: t.split(".")[0] + ".ab=cd." + t.split(".")[2],
    lambda t: t.split(".")[0] + ".ab cd." + t.split(".")[2],
    lambda t: t.split(".")[0] + "." + t.split(".")[1] + ".sig!",
    lambda t: t.split(".")[0] + "." + t.split(".")[1][:-1] + "\u00e9." + t.split(".")[2],
])
def test_characters_outside_the_base64url_alphabet_are_refused(mutate):
    token = mutate(token_for(traveler_id=DECOY))
    output = refused_with_headers({"Authorization": "Bearer " + token})
    assert refusal_text(output).startswith("Identity Check Failed: ")


def test_a_payload_that_decodes_only_after_dropping_characters_is_refused():
    good = b64(json.dumps({"token_use": "access", "traveler_id": DECOY}).encode())
    token = "e30." + good[:4] + "*" + good[4:] + ".sig"
    assert "not an access token" in refusal_text(
        refused_with_headers({"Authorization": "Bearer " + token})
    )


@pytest.mark.parametrize("event", [
    None, "text", {}, {"interceptorInputVersion": "2.0"},
    {"interceptorInputVersion": "1.0"}, {"interceptorInputVersion": "1.0", "mcp": []},
    {"interceptorInputVersion": "1.0", "mcp": {"gatewayRequest": {"body": []}}},
    {"interceptorInputVersion": "1.0", "http": {"gatewayRequest": {}}},
])
def test_a_malformed_event_is_refused_without_raising(event):
    output = interceptor.lambda_handler(event, None)
    assert "could not be read" in refusal_text(output)
    assert output["mcp"]["transformedGatewayResponse"]["body"]["id"] is None


@pytest.mark.parametrize("params", [None, [], {"arguments": {}}, {"name": 5, "arguments": {}},
                                    {"name": HOLD, "arguments": "x"}])
def test_a_tool_call_without_a_usable_name_and_arguments_is_refused(params):
    event = load("documented_tools_call_hold")
    event["mcp"]["gatewayRequest"]["body"]["params"] = params
    assert "could not be read" in refusal_text(interceptor.lambda_handler(event, None))


def test_an_unexpected_error_fails_closed(monkeypatch):
    def boom(_event):
        raise RuntimeError("boom with a secret detail")

    monkeypatch.setattr(interceptor, "pin_traveler", boom)
    output = interceptor.lambda_handler(load("documented_tools_call_hold"), None)
    assert "could not be completed" in refusal_text(output)
    assert "secret" not in json.dumps(output)


def test_nothing_sensitive_is_logged(caplog):
    token = token_for(traveler_id=DECOY)
    with caplog.at_level(logging.INFO):
        interceptor.lambda_handler(load("documented_tools_call_hold", token), None)
        interceptor.lambda_handler(load("documented_tools_call_hold", token_for()), None)
    assert token not in caplog.text and token.split(".")[1] not in caplog.text
    assert DECOY not in caplog.text


def test_an_unexpected_error_logs_only_the_exception_class(monkeypatch, caplog):
    def boom(_event):
        raise RuntimeError("secret detail " + DECOY)

    monkeypatch.setattr(interceptor, "pin_traveler", boom)
    with caplog.at_level(logging.INFO):
        interceptor.lambda_handler(load("documented_tools_call_hold"), None)
    assert "RuntimeError" in caplog.text
    assert "secret" not in caplog.text and DECOY not in caplog.text


def test_the_pinned_tool_set_can_be_replaced_for_the_harness(monkeypatch):
    monkeypatch.setenv("PINNED_TOOLS", "echo___echo, other___tool")
    assert interceptor.pinned_tools() == {"echo___echo", "other___tool"}
    event = load("documented_tools_call_hold", token_for(traveler_id=DECOY))
    unchanged = rewritten_body(interceptor.lambda_handler(event, None))
    assert unchanged["params"]["arguments"]["travelerId"] == JORDAN


def template_tools() -> dict:
    """Every tool of every Gateway target in the template, keyed by its Gateway tool name."""
    spec = json.loads(TEMPLATE.read_text())
    tools = {}
    for gateway in spec["agentCoreGateways"]:
        for target in gateway["targets"]:
            definitions = target.get("toolDefinitions")
            if definitions is None:
                folder = re.sub(r"(?<!^)(?=[A-Z])", "_", target["name"]).lower()
                folder = folder.removesuffix("_lambda")
                schema = TARGET_DIR / folder / "tool-schema.json"
                definitions = json.loads(schema.read_text())
            for tool in definitions:
                tools[f"{target['name']}___{tool['name']}"] = tool
    return tools


def test_every_template_tool_that_takes_a_traveler_is_pinned_by_default():
    tools = template_tools()
    assert len(tools) >= 4
    takes_traveler = {
        name for name, tool in tools.items() if "travelerId" in tool["inputSchema"]["properties"]
    }
    assert takes_traveler
    assert takes_traveler <= set(interceptor.DEFAULT_PINNED_TOOLS)
    assert set(interceptor.DEFAULT_PINNED_TOOLS) <= set(tools)


def test_the_search_tool_takes_no_traveler_so_it_is_never_pinned():
    schema = ROOT / "meridian_agentcore/agentcore/gateway_targets/semantic_trip_search"
    assert "travelerId" not in (schema / "tool-schema.json").read_text()


def test_a_token_without_a_usable_expiry_is_refused():
    for claims in ({"exp": None}, {"exp": "9999999999"}, {"exp": True}, {"exp": 1}):
        token = token_for(traveler_id=DECOY, **claims)
        output = refused_with_headers({"Authorization": "Bearer " + token})
        assert "expired or carries no expiry" in refusal_text(output)


def test_a_token_with_no_exp_claim_at_all_is_refused():
    payload = {"token_use": "access", "traveler_id": DECOY}
    token = f"{b64(b'{}')}.{b64(json.dumps(payload).encode())}.sig"
    output = refused_with_headers({"Authorization": "Bearer " + token})
    assert "expired or carries no expiry" in refusal_text(output)


@pytest.mark.parametrize("use", ["id", "refresh", None, 5])
def test_a_token_whose_use_is_not_access_is_refused(use):
    token = token_for(traveler_id=DECOY, token_use=use)
    output = refused_with_headers({"Authorization": "Bearer " + token})
    assert "not an access token" in refusal_text(output)


def test_the_expected_client_and_issuer_are_enforced_when_configured(monkeypatch):
    monkeypatch.setenv("EXPECTED_CLIENT_ID", "client-web")
    monkeypatch.setenv("EXPECTED_ISSUER", "https://issuer.example/pool")
    event = load("documented_tools_call_hold", token_for(traveler_id=DECOY))
    assert rewritten_body(interceptor.lambda_handler(event, None))
    for claims in ({"client_id": "other"}, {"iss": "https://evil.example"}, {"iss": None}):
        token = token_for(traveler_id=DECOY, **claims)
        output = refused_with_headers({"Authorization": "Bearer " + token})
        assert "not an access token" in refusal_text(output)


def test_empty_expected_client_and_issuer_skip_the_check(monkeypatch):
    monkeypatch.setenv("EXPECTED_CLIENT_ID", "")
    monkeypatch.setenv("EXPECTED_ISSUER", "")
    token = token_for(traveler_id=DECOY, client_id="anything", iss="anything")
    event = load("documented_tools_call_hold", token)
    assert rewritten_body(interceptor.lambda_handler(event, None))["params"]["arguments"][
        "travelerId"
    ] == DECOY


def test_a_pinned_call_does_not_share_its_arguments_with_the_input():
    event = load("documented_tools_call_hold")
    body = rewritten_body(interceptor.lambda_handler(event, None))
    assert body["params"]["arguments"] is not event["mcp"]["gatewayRequest"]["body"]["params"][
        "arguments"
    ]
