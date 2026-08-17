"""Unit tests for Bedrock AgentCore adapters (real API calls only when configured)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.agentcore.errors import AgentCoreNotConfiguredError
from backend.agentcore.gateway import (
    AgentCoreGatewayAdapter,
    _extract_packages_from_mcp_result,
    get_agentcore_gateway,
)
from backend.agentcore.memory import AgentCoreMemoryAdapter
from backend.agentcore.runtime import AgentCoreRuntimeAdapter, get_agentcore_runtime
from backend.agentcore import cli_config


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


def test_runtime_unconfigured_raises(unconfigured_agentcore):
    adapter = AgentCoreRuntimeAdapter(runtime_arn=None)
    with pytest.raises(AgentCoreNotConfiguredError):
        adapter.invoke_turn("conv-1", "trv_demo", "hello", "", [])


def test_runtime_configured_invoke_live():
    adapter = AgentCoreRuntimeAdapter(
        runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/x",
        region="us-east-1",
    )
    mock_client = MagicMock()
    mock_client.invoke_agent_runtime.return_value = {
        "response": [
            b'{"message":"Tokyo fits your saved preferences.",'
            b'"recommended_package_ids":["CTY-002"],'
            b'"follow_ups":["Compare options"]}'
        ]
    }
    adapter._client = mock_client

    decision = adapter.invoke_turn(
        "conv-2",
        "trv_demo",
        "Find Tokyo",
        "prefers boutique hotels",
        [{"package_id": "CTY-002", "name": "Tokyo Culture"}],
    )
    assert decision.invoke_status == "live"
    assert decision.message == "Tokyo fits your saved preferences."
    assert decision.recommended_package_ids == ["CTY-002"]
    assert len(decision.runtime_session_id) >= 33
    assert decision.runtime_session_id.startswith("rt-")
    mock_client.invoke_agent_runtime.assert_called_once()
    kwargs = mock_client.invoke_agent_runtime.call_args.kwargs
    assert kwargs["runtimeSessionId"] == decision.runtime_session_id
    payload = json.loads(kwargs["payload"])
    assert payload["event"] == "concierge_turn"
    assert payload["candidates"][0]["package_id"] == "CTY-002"


def test_runtime_unwraps_agentcore_sse_json_string():
    payload = json.dumps(
        {
            "message": "Tokyo fits your saved preferences.",
            "recommended_package_ids": ["TKY-003"],
            "follow_ups": ["Compare options"],
        }
    )

    parsed = AgentCoreRuntimeAdapter._parse_decision(
        f"data: {json.dumps(payload)}\n\n".encode()
    )

    assert parsed["message"] == "Tokyo fits your saved preferences."
    assert parsed["recommended_package_ids"] == ["TKY-003"]


def test_gateway_unconfigured_raises(unconfigured_agentcore):
    adapter = AgentCoreGatewayAdapter(gateway_url="")
    with pytest.raises(AgentCoreNotConfiguredError):
        adapter.list_tools()


def test_gateway_extract_packages_from_mcp_text_content():
    raw = {
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "packages": [
                                {"package_id": "CTY-002", "name": "Tokyo Culture & Cuisine"}
                            ]
                        }
                    ),
                }
            ]
        }
    }
    packages = _extract_packages_from_mcp_result(raw)
    assert len(packages) == 1
    assert packages[0]["package_id"] == "CTY-002"


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


def test_memory_namespace_matches_deployed_template():
    assert (
        AgentCoreMemoryAdapter._namespace("trv_demo", "conv_123")
        == "/users/trv_demo/sessions/conv_123"
    )


def test_memory_record_turn_uses_template_namespace():
    adapter = AgentCoreMemoryAdapter(memory_id="mem-abc", region="us-east-1")
    mock_client = MagicMock()
    mock_client.create_event.return_value = {"event": {"eventId": "evt-1"}}
    adapter._client = mock_client

    result = adapter.record_turn("trv_demo", "conv_123", "hello", "hi")

    assert result["event_id"] == "evt-1"
    kwargs = mock_client.create_event.call_args.kwargs
    assert kwargs["actorId"] == "trv_demo"
    assert kwargs["sessionId"] == "conv_123"
    assert kwargs["metadata"]["namespace"]["stringValue"] == "/users/trv_demo/sessions/conv_123"


def test_memory_reads_use_template_namespace():
    adapter = AgentCoreMemoryAdapter(memory_id="mem-abc", region="us-east-1")
    mock_client = MagicMock()
    mock_client.list_memory_records.return_value = {"memoryRecordSummaries": []}
    mock_client.retrieve_memory_records.return_value = {"memoryRecordSummaries": []}
    adapter._client = mock_client

    adapter.list_recent_turns("trv_demo", "conv_123")
    adapter.semantic_recall("trv_demo", "conv_123", "tokyo")

    assert (
        mock_client.list_memory_records.call_args.kwargs["namespace"]
        == "/users/trv_demo/sessions/conv_123"
    )
    assert (
        mock_client.retrieve_memory_records.call_args.kwargs["namespace"]
        == "/users/trv_demo/sessions/conv_123"
    )


def test_singleton_getters():
    assert get_agentcore_runtime() is get_agentcore_runtime()
    assert get_agentcore_gateway() is get_agentcore_gateway()
