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
from backend.agentcore.runtime import AgentCoreRuntimeAdapter, get_agentcore_runtime


def test_runtime_unconfigured_raises():
    adapter = AgentCoreRuntimeAdapter(runtime_arn=None)
    with pytest.raises(AgentCoreNotConfiguredError):
        adapter.session_for_turn("conv-1", "trv_demo")


def test_runtime_configured_invoke_live():
    adapter = AgentCoreRuntimeAdapter(
        runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/x",
        region="us-east-1",
    )
    mock_client = MagicMock()
    mock_client.invoke_agent_runtime.return_value = {"response": [b'{"ok":true}']}
    adapter._client = mock_client

    session = adapter.session_for_turn("conv-2", "trv_demo")
    assert session.invoke_status == "live"
    assert len(session.runtime_session_id) >= 33
    assert session.runtime_session_id.startswith("rt-")
    mock_client.invoke_agent_runtime.assert_called_once()
    kwargs = mock_client.invoke_agent_runtime.call_args.kwargs
    assert kwargs["runtimeSessionId"] == session.runtime_session_id


def test_gateway_unconfigured_raises():
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


def test_singleton_getters():
    assert get_agentcore_runtime() is get_agentcore_runtime()
    assert get_agentcore_gateway() is get_agentcore_gateway()
