"""Stdio client for the custom Meridian traveler-memory MCP server."""

from __future__ import annotations

from contextlib import AsyncExitStack

import asyncio
import os
import sys
from typing import Any, Dict, List, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from backend.mcp.concierge_mcp_client import _decode


def _server_params() -> StdioServerParameters:
    env = {
        "AWS_DEFAULT_REGION": os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        "AURORA_CLUSTER_ARN": os.getenv("AURORA_CLUSTER_ARN", ""),
        "AURORA_SECRET_ARN": os.getenv("AURORA_SECRET_ARN", ""),
        "AURORA_DATABASE": os.getenv("AURORA_DATABASE", "meridian"),
        "MCP_MEMORY_LOG_LEVEL": os.getenv("MCP_MEMORY_LOG_LEVEL", "WARNING"),
        "PYTHONPATH": os.getenv("PYTHONPATH", "."),
    }
    for key in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
    ):
        if os.getenv(key):
            env[key] = os.getenv(key, "")
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "backend.mcp.memory_server"],
        env=env,
    )


class MeridianMemoryMCPClient:
    """Thin async wrapper used by the stand-alone memory MCP demo."""

    def __init__(self) -> None:
        self.session: Optional[ClientSession] = None
        self._connected = False
        self._tools: List[Dict[str, Any]] = []
        self._exit_stack: Optional[AsyncExitStack] = None

    async def connect(self) -> None:
        if self._connected:
            return
        self._exit_stack = AsyncExitStack()
        try:
            read, write = await self._exit_stack.enter_async_context(stdio_client(_server_params()))
            self.session = await self._exit_stack.enter_async_context(ClientSession(read, write))
            await self.session.initialize()
            response = await self.session.list_tools()
            self._tools = [{"name": t.name, "description": t.description} for t in response.tools]
            self._connected = True
        except BaseException:
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        """Unwind in the owning task; MCP bounds subprocess termination itself."""
        stack, self._exit_stack = self._exit_stack, None
        self._connected = False
        self.session = None
        if stack is not None:
            await stack.aclose()

    async def call(
        self,
        tool: str,
        arguments: Dict[str, Any],
        timeout: float = 20.0,
    ) -> Any:
        if not self._connected:
            await self.connect()
        result = await asyncio.wait_for(
            self.session.call_tool(tool, arguments),
            timeout=timeout,
        )
        decoded = _decode(result)
        if getattr(result, "isError", False):
            raise PermissionError(str(decoded))
        return decoded

    @property
    def tools(self) -> List[Dict[str, Any]]:
        return self._tools
