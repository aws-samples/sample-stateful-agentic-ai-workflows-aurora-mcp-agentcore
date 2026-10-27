"""Stdio cancel scopes must close in their owning task, even on startup failure."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.mcp import concierge_mcp_client, memory_mcp_client


@pytest.mark.asyncio
@pytest.mark.parametrize("module,client_type", [
    (concierge_mcp_client, concierge_mcp_client.MeridianConciergeMCPClient),
    (memory_mcp_client, memory_mcp_client.MeridianMemoryMCPClient),
])
@pytest.mark.parametrize("fail_discovery", [False, True])
async def test_transport_unwinds_in_owner_task(monkeypatch, module, client_type, fail_discovery):
    monkeypatch.setenv("AURORA_CLUSTER_ARN", "arn:aws:rds:us-east-1:000000000000:cluster:test")
    monkeypatch.setenv("AURORA_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:000000000000:secret:test")
    events = []
    owner = asyncio.current_task()
    session = SimpleNamespace(
        initialize=AsyncMock(),
        list_tools=AsyncMock(return_value=SimpleNamespace(tools=[])),
    )
    if fail_discovery:
        session.list_tools.side_effect = RuntimeError("discovery failed")

    @asynccontextmanager
    async def transport(_params):
        events.append(("transport enter", asyncio.current_task()))
        try:
            yield "read", "write"
        finally:
            events.append(("transport exit", asyncio.current_task()))

    @asynccontextmanager
    async def session_context(*_args):
        events.append(("session enter", asyncio.current_task()))
        try:
            yield session
        finally:
            events.append(("session exit", asyncio.current_task()))

    monkeypatch.setattr(module, "stdio_client", transport)
    monkeypatch.setattr(module, "ClientSession", session_context)
    client = client_type()
    if fail_discovery:
        with pytest.raises(RuntimeError, match="discovery failed"):
            await client.connect()
    else:
        await client.connect()
    await client.disconnect()
    await client.disconnect()  # Cleanup remains safe after a partial failure.
    assert events == [(name, owner) for name in (
        "transport enter", "session enter", "session exit", "transport exit",
    )]
    assert client.session is None


@pytest.mark.asyncio
async def test_concierge_tool_error_is_not_reported_as_no_matching_trips():
    client = concierge_mcp_client.MeridianConciergeMCPClient()
    client._connected = True
    client.session = SimpleNamespace(call_tool=AsyncMock(
        return_value=SimpleNamespace(isError=True, content=[]),
    ))
    with pytest.raises(RuntimeError, match="Meridian concierge MCP tool failed"):
        await client.call("price_range", {"destination": "Tokyo"})
