"""Unit tests for Bedrock AgentCore adapters (real API calls only when configured)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.agentcore import cli_config
from backend.agentcore.errors import AgentCoreNotConfiguredError
from backend.agentcore.gateway import AgentCoreGatewayAdapter, get_agentcore_gateway
from backend.agentcore.runtime import AgentCoreRuntimeAdapter, get_agentcore_runtime, parse_sse


@pytest.fixture
def unconfigured_agentcore(tmp_path, monkeypatch):
    """Force the genuinely-unconfigured path regardless of any local deploy.

    Points AGENTCORE_PROJECT_DIR at an empty temp dir (so no deployed-state.json
    is found), skips the live `agentcore status` subprocess, clears the env-var
    overrides, and resets the resolve_agentcore_config lru_cache — so a developer's
    real deploy in the repo doesn't leak into tests of the unconfigured path.
    """
    monkeypatch.setenv("AGENTCORE_PROJECT_DIR", str(tmp_path / "agentcore"))
    monkeypatch.setenv("AGENTCORE_SKIP_CLI_SYNC", "1")
    for var in ("AGENTCORE_RUNTIME_ARN", "AGENTCORE_GATEWAY_URL", "AGENTCORE_MEMORY_ID"):
        monkeypatch.delenv(var, raising=False)
    cli_config.resolve_agentcore_config.cache_clear()
    yield
    cli_config.resolve_agentcore_config.cache_clear()


def _adapter():
    return AgentCoreRuntimeAdapter(
        runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/x", region="us-east-1"
    )


def _streaming_client(*chunks: bytes):
    body = MagicMock()
    body.read.side_effect = [*chunks, b""]
    client = MagicMock()
    client.invoke_agent_runtime.return_value = {
        "response": body,
        "ResponseMetadata": {"RequestId": "r"},
    }
    return client


def test_runtime_unconfigured_raises(unconfigured_agentcore):
    adapter = AgentCoreRuntimeAdapter(runtime_arn=None)
    with pytest.raises(AgentCoreNotConfiguredError):
        adapter.invoke_turn("conv-1", "trv_demo", "hello", "", budget_ceiling_cents=0, travelers_count=1)


def test_parse_sse_unwraps_the_double_encoded_json_lines():
    inner = json.dumps({"type": "activity", "title": "x"})
    raw = (
        f"data: {json.dumps(inner)}\n\n"
        'data: {"type": "result", "message": "Tokyo fits.", "recommended_package_ids": ["CTY-002"], '
        '"follow_ups": [], "hold": null, "trace_id": "abc", "usage": {}, "elapsed_ms": 12}\n\n'
        ": keepalive\n\n"
    ).encode()
    events = parse_sse(raw)
    assert [event["type"] for event in events] == ["activity", "result"]
    assert events[0]["title"] == "x"


def test_runtime_invoke_collects_spans_packages_and_hold():
    adapter = _adapter()
    adapter._client = _streaming_client(
        b'data: {"type": "activity", "id": "a1", "timestamp": "t", "activity_type": "search", '
        b'"title": "s"}\n\n'
        b'data: {"type": "packages", "packages": [{"package_id": "CTY-002", "name": "Tokyo"}]}\n\n'
        b'data: {"type": "hold", "hold": {"bookingId": "HLD-1", "status": "held"}, '
        b'"policyDecision": "allow"}\n\n',
        b'data: {"type": "token", "text": "Held."}\n\n'
        b'data: {"type": "result", "message": "Held.", "recommended_package_ids": ["CTY-002"], '
        b'"follow_ups": ["Compare"], "hold": {"bookingId": "HLD-1", "status": "held"}, '
        b'"trace_id": "abc", "usage": {"inputTokens": 1}, "elapsed_ms": 5}\n\n',
    )
    decision = adapter.invoke_turn(
        "conv-1",
        "trv_demo",
        "hold it",
        "ctx",
        budget_ceiling_cents=700000,
        travelers_count=2,
        hold_confirmed=True,
        hold_target={
            "package_id": "CTY-002",
            "duration": "7 nights",
            "travelers": 2,
            "unit_price_cents": 250000,
        },
    )
    assert decision.message == "Held."
    assert decision.packages[0]["package_id"] == "CTY-002"
    assert decision.recommended_package_ids == ["CTY-002"]
    assert decision.hold["bookingId"] == "HLD-1"
    assert decision.policy_decision == "allow"
    assert decision.trace_id == "abc"
    assert decision.usage == {"inputTokens": 1}
    assert [span["title"] for span in decision.activities] == ["s"]
    assert "type" not in decision.activities[0]
    assert len(decision.runtime_session_id) >= 33
    kwargs = adapter._client.invoke_agent_runtime.call_args.kwargs
    assert kwargs["accept"] == "text/event-stream"
    assert kwargs["runtimeSessionId"] == decision.runtime_session_id
    payload = json.loads(kwargs["payload"])
    assert payload["event"] == "concierge_turn"
    assert payload["hold_confirmed"] is True
    assert payload["budget_ceiling_cents"] == 700000
    assert payload["hold_target"]["package_id"] == "CTY-002"


def test_runtime_refused_hold_keeps_the_refusal_and_the_policy_decision():
    adapter = _adapter()
    adapter._client = _streaming_client(
        b'data: {"type": "hold", "hold": null, "refused": "Refused by Cedar policy x.", '
        b'"policyDecision": "deny"}\n\n'
        b'data: {"type": "result", "message": "I could not hold it.", '
        b'"recommended_package_ids": [], "follow_ups": [], "hold": null, "trace_id": null, '
        b'"usage": {}, "elapsed_ms": 1}\n\n',
    )
    decision = adapter.invoke_turn(
        "conv-1", "trv_demo", "hold it", "", budget_ceiling_cents=1, travelers_count=1
    )
    assert decision.hold is None
    assert decision.hold_refused == "Refused by Cedar policy x."
    assert decision.policy_decision == "deny"


def test_runtime_error_event_raises():
    adapter = _adapter()
    adapter._client = _streaming_client(b'data: {"type": "error", "message": "boom"}\n\n')
    with pytest.raises(RuntimeError, match="boom"):
        adapter.invoke_turn("conv-1", "trv_demo", "x", "", budget_ceiling_cents=0, travelers_count=1)


def test_runtime_without_a_message_raises():
    adapter = _adapter()
    adapter._client = _streaming_client(b'data: {"type": "token", "text": "partial"}\n\n')
    with pytest.raises(RuntimeError, match="no concierge message"):
        adapter.invoke_turn("conv-1", "trv_demo", "x", "", budget_ceiling_cents=0, travelers_count=1)


def test_gateway_unconfigured_raises(unconfigured_agentcore):
    adapter = AgentCoreGatewayAdapter(gateway_url="")
    with pytest.raises(AgentCoreNotConfiguredError):
        adapter.list_tools()


@patch("backend.agentcore.gateway.urllib.request.urlopen")
def test_gateway_mcp_tools_list(mock_urlopen):
    adapter = AgentCoreGatewayAdapter(
        gateway_url="https://gw.example.com/mcp",
        access_token="test-token",
    )
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(
        {"result": {"tools": [{"name": "search___trip", "description": "Search trips"}]}}
    ).encode()
    mock_resp.__enter__.return_value = mock_resp
    mock_urlopen.return_value = mock_resp

    tools, _raw = adapter.list_tools()
    assert len(tools) == 1
    assert tools[0]["name"] == "search___trip"


def test_singleton_getters():
    assert get_agentcore_runtime() is get_agentcore_runtime()
    assert get_agentcore_gateway() is get_agentcore_gateway()
