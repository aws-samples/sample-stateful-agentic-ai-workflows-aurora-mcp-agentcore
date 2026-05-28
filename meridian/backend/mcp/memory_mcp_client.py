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
        """Idempotent teardown - see concierge_mcp_client for rationale."""
        import asyncio

        session_ctx = self._session_context
        stdio_ctx = self._stdio_context
        self._connected = False
        self.session = None
        self._session_context = None
        self._stdio_context = None

        async def _close_session():
            if session_ctx is not None:
                try:
                    await session_ctx.__aexit__(None, None, None)
                except Exception:
                    pass

        async def _close_stdio():
            if stdio_ctx is not None:
                try:
                    await stdio_ctx.__aexit__(None, None, None)
                except Exception:
                    pass

        try:
            await asyncio.wait_for(_close_session(), timeout=3.0)
        except asyncio.TimeoutError:
            pass
        try:
            await asyncio.wait_for(_close_stdio(), timeout=3.0)
        except asyncio.TimeoutError:
            pass

    async def call(self, tool: str, arguments: Dict[str, Any], timeout: float = 15.0) -> Any:
        """Call a tool with a hard timeout (see concierge_mcp_client._call docs)."""
        import asyncio

        if not self._connected:
            await self.connect()
        try:
            result = await asyncio.wait_for(
                self.session.call_tool(tool, arguments),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "meridian-memory tool %s timed out after %.1fs - resetting client",
                tool,
                timeout,
            )
            try:
                await self.disconnect()
            except Exception:
                self._connected = False
                self.session = None
            raise
        return _decode(result)

    @property
    def tools(self) -> List[Dict[str, Any]]:
        return self._tools


def _decode(result: Any) -> Any:
    """Decode an MCP tool result into a python value.

    Handles three FastMCP shapes:
      - structuredContent (newer servers, richest)
      - single TextContent block with full JSON payload (most common)
      - multiple TextContent blocks, one per list element
    """
    import json

    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        if isinstance(structured, dict) and "result" in structured and len(structured) == 1:
            return structured["result"]
        return structured

    if not hasattr(result, "content") or not result.content:
        return None

    text_blocks: list[str] = []
    for content in result.content:
        if hasattr(content, "text") and content.text:
            text_blocks.append(content.text)

    if not text_blocks:
        return None

    joined = "".join(text_blocks)
    try:
        return json.loads(joined)
    except json.JSONDecodeError:
        pass

    decoded: list[Any] = []
    for block in text_blocks:
        try:
            decoded.append(json.loads(block))
        except json.JSONDecodeError:
            decoded.append(block)
    if len(decoded) == 1:
        return decoded[0]
    return decoded


# Per-turn subprocess (see concierge_mcp_client.py for rationale).
@asynccontextmanager
async def memory_mcp_session():
    client = MeridianMemoryMCPClient()
    try:
        await client.connect()
        yield client
    finally:
        try:
            await client.disconnect()
        except Exception as exc:
            logger.warning("memory MCP disconnect raised: %s", exc)


def get_memory_mcp_client() -> MeridianMemoryMCPClient:
    """Backward-compat shim - new code should use `memory_mcp_session()`."""
    return MeridianMemoryMCPClient()
