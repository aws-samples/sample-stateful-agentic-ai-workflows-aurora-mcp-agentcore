"""
Stdio integration test for the custom Meridian memory MCP server.

We launch the real server subprocess, perform the MCP handshake, and call
`list_memory_tools` (a tool that requires no Aurora connectivity).  This
catches:

  - import errors in `backend.mcp.memory_server`
  - FastMCP regressions (renamed attributes, etc.)
  - registration drift between the @mcp.tool decorators and the listing

It does NOT exercise the Aurora-bound tools — those live in the
`examples/memory_mcp_demo.py` walkthrough that the workshop facilitator
runs against the real cluster.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


EXPECTED_TOOLS = {
    "recall_traveler_profile",
    "recall_preferences",
    "recall_recent_turns",
    "semantic_recall_interactions",
    "persist_turn",
    "persist_preference",
    "list_memory_tools",
}


async def _list_and_call() -> dict:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "backend.mcp.memory_server"],
        env={
            "PYTHONPATH": os.getenv("PYTHONPATH", "."),
            "AWS_DEFAULT_REGION": "us-east-1",
            "AURORA_CLUSTER_ARN": "stub",
            "AURORA_SECRET_ARN": "stub",
            "AURORA_DATABASE": "meridian",
        },
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            names = {t.name for t in tools.tools}
            res = await s.call_tool("list_memory_tools", {})
            payload = []
            for c in res.content:
                if hasattr(c, "text"):
                    try:
                        payload.append(json.loads(c.text))
                    except json.JSONDecodeError:
                        pass
            return {"names": names, "self_describe": payload}


def test_memory_mcp_server_handshake_and_tool_listing() -> None:
    result = asyncio.run(asyncio.wait_for(_list_and_call(), timeout=20))
    assert EXPECTED_TOOLS.issubset(result["names"]), (
        f"missing tools: {EXPECTED_TOOLS - result['names']}"
    )
    described = {item.get("name") for item in result["self_describe"] if isinstance(item, dict)}
    assert {"recall_traveler_profile", "persist_turn"}.issubset(described)
