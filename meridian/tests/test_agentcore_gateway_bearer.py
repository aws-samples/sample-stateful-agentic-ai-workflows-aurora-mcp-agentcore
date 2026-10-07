"""The Gateway client signs with IAM by default and sends the caller's token in jwt mode."""

import io
import json
import urllib.error
from unittest.mock import patch

import pytest

from backend.agentcore import gateway as gateway_module
from backend.agentcore.caller_credential import caller_token_scope
from backend.agentcore.errors import CallerTokenExpired, CallerTokenMissing
from backend.agents.phase_05_workflow.governed_hold import place_governed_hold
from tests.jwt_support import access_token

URL = "https://gw-1.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.delenv("AGENTCORE_GATEWAY_ACCESS_TOKEN", raising=False)
    return gateway_module.AgentCoreGatewayAdapter(gateway_url=URL, region="us-east-1")


class Reply:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def sent_request(urlopen):
    return urlopen.call_args.args[0]


def test_iam_mode_still_signs_with_sigv4_and_sends_no_bearer(adapter):
    with patch.object(gateway_module.urllib.request, "urlopen", return_value=Reply({})) as urlopen:
        adapter.call_tool("t", {})
    headers = {k.lower(): v for k, v in sent_request(urlopen).header_items()}
    assert headers["authorization"].startswith("AWS4-HMAC-SHA256")
    assert "x-amz-date" in headers


def test_jwt_mode_sends_the_callers_token_and_nothing_signed(adapter, monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    token = access_token()
    with caller_token_scope(token), patch.object(
        gateway_module.urllib.request, "urlopen", return_value=Reply({"result": {}})
    ) as urlopen:
        adapter.call_tool("t", {"a": 1})
    headers = {k.lower(): v for k, v in sent_request(urlopen).header_items()}
    assert headers["authorization"] == f"Bearer {token}"
    assert "x-amz-date" not in headers and "x-amz-security-token" not in headers


def test_the_static_gateway_token_override_is_ignored_in_jwt_mode(monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    adapter = gateway_module.AgentCoreGatewayAdapter(
        gateway_url=URL, region="us-east-1", access_token="static-override")
    with caller_token_scope(access_token()), patch.object(
        gateway_module.urllib.request, "urlopen", return_value=Reply({})
    ) as urlopen:
        adapter.call_tool("t", {})
    assert "static-override" not in sent_request(urlopen).get_header("Authorization")


def test_jwt_mode_without_a_caller_token_fails_before_any_network_call(adapter, monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    with patch.object(gateway_module.urllib.request, "urlopen") as urlopen:
        with pytest.raises(CallerTokenMissing):
            adapter.call_tool("t", {})
    urlopen.assert_not_called()


def test_an_expired_token_is_a_coded_expiry_before_any_network_call(adapter, monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    with caller_token_scope(access_token(expires_in=-5)), patch.object(
        gateway_module.urllib.request, "urlopen"
    ) as urlopen:
        with pytest.raises(CallerTokenExpired):
            adapter.call_tool("t", {})
    urlopen.assert_not_called()


def http_error(code):
    return urllib.error.HTTPError(URL, code, "x", {}, io.BytesIO(b'{"message":"nope"}'))


def test_a_401_after_the_token_ran_out_in_flight_is_a_coded_expiry(adapter, monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    checks = iter([None, CallerTokenExpired("expired in flight")])

    def ensure_unexpired(token):
        outcome = next(checks)
        if outcome:
            raise outcome

    monkeypatch.setattr(gateway_module, "ensure_unexpired", ensure_unexpired)
    with caller_token_scope(access_token()), patch.object(
        gateway_module.urllib.request, "urlopen", side_effect=http_error(401)
    ):
        with pytest.raises(CallerTokenExpired):
            adapter.call_tool("t", {})


def test_a_401_for_a_live_token_is_a_plain_gateway_error(adapter, monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    with caller_token_scope(access_token()), patch.object(
        gateway_module.urllib.request, "urlopen", side_effect=http_error(401)
    ):
        with pytest.raises(RuntimeError, match="Gateway HTTP 401"):
            adapter.call_tool("t", {})


def test_an_interceptor_refusal_is_a_refusal_not_an_unknown_outcome():
    refusal = {"jsonrpc": "2.0", "id": 1, "result": {"isError": True, "content": [
        {"type": "text", "text": "Identity Check Failed: the access token carries no single "
                                 "traveler for this system."}]}}
    outcome = place_governed_hold(lambda *_: refusal, {"packageId": "TKY-003"})
    assert not outcome.placed and outcome.policy_decision == "deny"
    assert outcome.error.startswith("Identity Check Failed:")
