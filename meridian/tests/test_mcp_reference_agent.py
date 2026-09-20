"""The Phase 2 reference agent must use the real Strands MCP client contract.

``strands.tools.mcp.MCPClient`` takes one positional transport factory and is
driven with ``start()`` / ``list_tools_sync()`` / ``stop()``. The reference
used a ``server_name=/command=/args=`` constructor and ``await connect()``
that never existed, so anyone copying it on stage got a TypeError.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from strands.tools.mcp import MCPClient

from backend.agents.mcp_02 import agent as mcp_agent


class _FakeClient:
    def __init__(self, transport_factory):
        assert callable(transport_factory)
        self.transport_factory = transport_factory
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True
        return self

    def list_tools_sync(self):
        return [SimpleNamespace(tool_name="run_query")]

    def stop(self, exc_type, exc_val, exc_tb):
        self.stopped = True


def test_reference_agent_builds_the_client_with_a_transport_factory(monkeypatch) -> None:
    monkeypatch.setenv("AURORA_CLUSTER_ARN", "arn:aws:rds:us-east-1:000000000000:cluster:x")
    monkeypatch.setenv("AURORA_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:000000000000:secret:y")
    monkeypatch.setattr(mcp_agent, "BedrockModel", lambda **kwargs: None)
    monkeypatch.setattr(mcp_agent, "Agent", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(mcp_agent, "MCPClient", _FakeClient)
    captured: dict = {}

    def fake_stdio_client(params):
        captured["params"] = params
        return SimpleNamespace(params=params)

    monkeypatch.setattr(mcp_agent, "stdio_client", fake_stdio_client)

    agent = mcp_agent.MCPAgent()
    agent.mcp_client.transport_factory()
    params = captured["params"]
    assert params.command == "uvx"
    assert params.args[0] == "awslabs.postgres-mcp-server@1.0.9"
    assert "--readonly=True" in params.args
    assert any(arg.startswith("--resource_arn=arn:aws:rds") for arg in params.args)

    agent._initialize_agent()
    assert agent.mcp_client.started
    assert agent.agent.tools[0].tool_name == "run_query"
    agent.close()
    assert agent.mcp_client.stopped


def test_reference_agent_matches_the_installed_strands_signature() -> None:
    # If Strands ever changes the constructor, this names the drift directly.
    params = list(inspect.signature(MCPClient.__init__).parameters)
    assert params[1] == "transport_callable"
    assert {"start", "stop", "list_tools_sync"} <= set(dir(MCPClient))
