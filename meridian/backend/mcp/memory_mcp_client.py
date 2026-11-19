"""Stdio client for the custom Meridian traveler-memory MCP server."""

from __future__ import annotations

from contextlib import AsyncExitStack

import asyncio
import os
import re
import sys
from typing import Any, Dict, List, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from backend.mcp.concierge_mcp_client import _decode
from backend.mcp.subprocess_env import aws_subprocess_env


# The refusal ``TravelerAuthorizationError`` carries when the workload has no active binding for
# the requested traveler. FastMCP prefixes it with "Error executing tool <name>: ".
BINDING_DENIAL = re.compile(r"subject is not authorized for traveler (\S+)")


class MemoryToolError(RuntimeError):
    """A memory tool failed for a reason other than the traveler-binding denial."""


class TravelerNotAuthorized(PermissionError):
    """The server refused the traveler claim because the workload has no binding for it."""

    def __init__(self, traveler_id: str, message: str) -> None:
        super().__init__(message)
        self.traveler_id = traveler_id


def tool_error(message: str) -> Exception:
    """The exception for a tool error text: a binding denial, or any other failure."""
    denial = BINDING_DENIAL.search(message)
    if denial:
        return TravelerNotAuthorized(denial.group(1).rstrip(".,;:)"), message)
    return MemoryToolError(message)


def _server_params() -> StdioServerParameters:
    env = {
        **aws_subprocess_env(),
        "AWS_DEFAULT_REGION": os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        "AURORA_CLUSTER_ARN": os.getenv("AURORA_CLUSTER_ARN", ""),
        "AURORA_SECRET_ARN": os.getenv("AURORA_SECRET_ARN", ""),
        "AURORA_DATABASE": os.getenv("AURORA_DATABASE", "meridian"),
        "MCP_MEMORY_LOG_LEVEL": os.getenv("MCP_MEMORY_LOG_LEVEL", "WARNING"),
        "PYTHONPATH": os.getenv("PYTHONPATH", "."),
    }
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
            raise tool_error(str(decoded))
        return decoded

    @property
    def tools(self) -> List[Dict[str, Any]]:
        return self._tools
