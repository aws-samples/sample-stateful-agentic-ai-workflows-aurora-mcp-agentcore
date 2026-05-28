"""
MCP client for the custom Meridian concierge server (`backend.mcp.concierge_server`).

This is the symmetric counterpart to `mcp_client.py` (postgres-mcp-server)
and `memory_mcp_client.py` (Phase 4 memory MCP). All three use the same
stdio transport - the agent doesn't know or care which one is which,
which is the whole point of MCP.

Phase 2's `mcp_search` attaches both this server and postgres-mcp-server
so the trace shows two distinct MCP servers feeding one agent turn:

    awslabs.postgres-mcp-server  → generic SQL transport
    meridian-concierge (custom)  → travel-specific domain tools
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
    """Launch the concierge server in-process via `python -m`."""
    env = {
        "AWS_DEFAULT_REGION": os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        "AURORA_CLUSTER_ARN": os.getenv("AURORA_CLUSTER_ARN", ""),
        "AURORA_SECRET_ARN": os.getenv("AURORA_SECRET_ARN", ""),
        "AURORA_DATABASE": os.getenv("AURORA_DATABASE", "meridian"),
        "MCP_CONCIERGE_LOG_LEVEL": os.getenv("MCP_CONCIERGE_LOG_LEVEL", "WARNING"),
        "PYTHONPATH": os.getenv("PYTHONPATH", ""),
    }
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE"):
        if os.getenv(k):
            env[k] = os.getenv(k)

    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "backend.mcp.concierge_server"],
        env=env,
    )


class MeridianConciergeMCPClient:
    """Thin async wrapper around the meridian-concierge MCP server."""

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
        logger.info(
            "connected to meridian-concierge MCP - tools: %s",
            [t["name"] for t in self._tools],
        )

    async def disconnect(self) -> None:
        """Tear down the session + subprocess. Idempotent; absorbs any
        stack-level errors raised by anyio's cancel-scope checks (which
        can fire when the disconnect happens on a different task than
        the connect)."""
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

        # Bound the teardown so a wedged subprocess can't block the next
        # turn from spawning a fresh client.
        try:
            await asyncio.wait_for(_close_session(), timeout=3.0)
        except asyncio.TimeoutError:
            pass
        try:
            await asyncio.wait_for(_close_stdio(), timeout=3.0)
        except asyncio.TimeoutError:
            pass

    async def call(self, tool: str, arguments: Dict[str, Any], timeout: float = 15.0) -> Any:
        """Call a tool with a hard timeout.

        Without the timeout, a stuck subprocess (Aurora connection wedged,
        stdio buffer backed up, etc.) would hang the FastAPI request
        forever. We give it 15s and force-disconnect on timeout so the
        next call gets a clean session instead of inheriting the wedge.
        """
        import asyncio

        if not self._connected:
            await self.connect()
        logger.info("[concierge MCP] -> %s args=%s", tool, arguments)
        try:
            result = await asyncio.wait_for(
                self.session.call_tool(tool, arguments),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[concierge MCP] %s TIMEOUT after %.1fs - resetting client",
                tool,
                timeout,
            )
            try:
                await self.disconnect()
            except Exception:
                self._connected = False
                self.session = None
            raise
        decoded = _decode(result)
        logger.info(
            "[concierge MCP] <- %s shape=%s preview=%r",
            tool,
            type(decoded).__name__,
            (str(decoded)[:160] if decoded is not None else None),
        )
        return decoded

    @property
    def tools(self) -> List[Dict[str, Any]]:
        return self._tools


def _decode(result: Any) -> Any:
    """Decode an MCP tool result into a python value (list/dict/str).

    FastMCP can return tool results in a few different shapes:
      - a single TextContent block containing JSON for the entire return
        value (most common when the tool returns a list/dict directly)
      - multiple TextContent blocks (one per element of a returned list)
      - a `structuredContent` field on the response itself

    The previous version returned the first parsed text block, which
    silently dropped the tail of multi-block responses (turning a list
    of 3 packages into a single dict and breaking downstream type
    checks). Try `structuredContent` first, then concatenate text
    blocks into a single JSON-decoded value.
    """
    import json

    # Newer MCP servers attach a `structuredContent` for richer typing.
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        # The MCP spec wraps the actual return in a {"result": ...}
        # envelope when the value isn't a dict at the top level.
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

    # Try the concatenated text as a single JSON document first - this
    # is the canonical FastMCP shape for non-string return values.
    joined = "".join(text_blocks)
    try:
        return json.loads(joined)
    except json.JSONDecodeError:
        pass

    # Fall back to decoding each block individually and stitching the
    # results into a list (covers the multi-block list case).
    decoded: list[Any] = []
    for block in text_blocks:
        try:
            decoded.append(json.loads(block))
        except json.JSONDecodeError:
            decoded.append(block)
    if len(decoded) == 1:
        return decoded[0]
    return decoded


# Per-turn subprocess. We previously kept a long-lived singleton client
# to avoid spawning Python on every tool call, but that bit us on stage:
# the second turn would block forever because the cached stdio session
# had wedged after the first turn (buffered output, half-closed pipe,
# Aurora connection state, etc.). For a workshop demo, predictability
# matters more than the ~150ms saved by reusing a process - so each
# session spawns a fresh subprocess and tears it down on exit.
@asynccontextmanager
async def concierge_mcp_session():
    client = MeridianConciergeMCPClient()
    try:
        await client.connect()
        yield client
    finally:
        try:
            await client.disconnect()
        except Exception as exc:
            logger.warning("concierge MCP disconnect raised: %s", exc)


# Backward-compat shim - some older code paths still call this. Returns
# a one-shot client that callers must connect/disconnect themselves;
# new code should use the `concierge_mcp_session()` context manager.
def get_concierge_mcp_client() -> MeridianConciergeMCPClient:
    return MeridianConciergeMCPClient()
