"""
MCP client for the custom Meridian memory server (`backend.mcp.memory_server`).

This is the *symmetric* counterpart to `mcp_client.py`:

    mcp_client.py            → connects to awslabs.postgres-mcp-server (raw SQL)
    memory_mcp_client.py     → connects to our own meridian-memory server
                                (recall/persist preferences and turns)

Phase 4's concierge can wire both at the same time so the demo shows two
distinct MCP servers feeding one agent — exactly what the abstract claims
("MCP servers for contextual memory").

Transport: stdio.  We launch the server as a subprocess via the same
StdioServerParameters the postgres MCP client uses, but pointing at our
own python module instead of the awslabs uvx package.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)


def _server_params() -> StdioServerParameters:
    """Launch the memory server in-process via `python -m`.

    Pass through everything the server needs to reach Aurora.  The server
    itself uses RDS Data API, so the only required env vars are the cluster
    + secret ARNs and a region.
    """
    env = {
        "AWS_DEFAULT_REGION": os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        "AURORA_CLUSTER_ARN": os.getenv("AURORA_CLUSTER_ARN", ""),
        "AURORA_SECRET_ARN": os.getenv("AURORA_SECRET_ARN", ""),
        "AURORA_DATABASE": os.getenv("AURORA_DATABASE", "meridian"),
        "MCP_MEMORY_LOG_LEVEL": os.getenv("MCP_MEMORY_LOG_LEVEL", "WARNING"),
        "PYTHONPATH": os.getenv("PYTHONPATH", ""),
    }
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE"):
        if os.getenv(k):
            env[k] = os.getenv(k)

    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "backend.mcp.memory_server"],
        env=env,
    )


class MeridianMemoryMCPClient:
    """Thin async wrapper around the meridian-memory MCP server."""

    def __init__(self) -> None:
        self.session: Optional[ClientSession] = None
        self._connected = False
        self._tools: List[Dict[str, Any]] = []
        self._stdio_context = None
        self._session_context = None

    async def connect(self) -> None:
        if self._connected:
            return
        params = _server_params()
        self._stdio_context = stdio_client(params)
        read, write = await self._stdio_context.__aenter__()
        self._session_context = ClientSession(read, write)
        self.session = await self._session_context.__aenter__()
        await self.session.initialize()
        resp = await self.session.list_tools()
        self._tools = [
            {"name": t.name, "description": t.description} for t in resp.tools
        ]
        self._connected = True
        logger.info("connected to meridian-memory MCP — tools: %s", [t["name"] for t in self._tools])

    async def disconnect(self) -> None:
        if not self._connected:
            return
        try:
            if self._session_context:
                await self._session_context.__aexit__(None, None, None)
        finally:
            if self._stdio_context:
                await self._stdio_context.__aexit__(None, None, None)
            self._connected = False
            self.session = None

    async def call(self, tool: str, arguments: Dict[str, Any]) -> Any:
        if not self._connected:
            await self.connect()
        result = await self.session.call_tool(tool, arguments)
        return _decode(result)

    @property
    def tools(self) -> List[Dict[str, Any]]:
        return self._tools


def _decode(result: Any) -> Any:
    """Decode an MCP tool result into a python value (list/dict/str)."""
    import json

    if not hasattr(result, "content") or not result.content:
        return None
    for content in result.content:
        if hasattr(content, "text"):
            try:
                return json.loads(content.text)
            except json.JSONDecodeError:
                return content.text
    return None


_client: Optional[MeridianMemoryMCPClient] = None


def get_memory_mcp_client() -> MeridianMemoryMCPClient:
    global _client
    if _client is None:
        _client = MeridianMemoryMCPClient()
    return _client


@asynccontextmanager
async def memory_mcp_session():
    client = get_memory_mcp_client()
    try:
        await client.connect()
        yield client
    finally:
        # keep alive for reuse — the demo is single-process
        pass
