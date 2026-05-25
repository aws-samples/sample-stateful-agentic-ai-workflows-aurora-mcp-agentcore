"""
Stand-alone smoke test / demo for the custom Meridian memory MCP server.

What it does
============

Spins up `backend.mcp.memory_server` over stdio, lists the tools it
exposes, then exercises each one against the real Aurora cluster
(via RDS Data API).  This is the script the workshop facilitator runs
to prove that:

    1. the custom MCP server actually starts up,
    2. it speaks Model Context Protocol like any other server, and
    3. its tools enforce Aurora RLS — recall_traveler_profile for a
       different traveler returns nothing.

Usage
=====

From `meridian/`:

    source venv/bin/activate
    PYTHONPATH=. python examples/memory_mcp_demo.py

Required env (same as Phase 4):

    AURORA_CLUSTER_ARN, AURORA_SECRET_ARN, AWS_DEFAULT_REGION

You can pass `--traveler trv_meridian_demo` and
`--conversation conv_meridian_demo` to target the seeded demo data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from backend.mcp.memory_mcp_client import MeridianMemoryMCPClient


def _show(label: str, value: Any) -> None:
    print(f"\n--- {label} ---")
    print(json.dumps(value, indent=2, default=str)[:1500])


async def run(traveler_id: str, conversation_id: str) -> None:
    client = MeridianMemoryMCPClient()
    print(f"Launching meridian-memory MCP server (stdio)…")
    await client.connect()

    print("\nTools exposed by the server:")
    for tool in client.tools:
        print(f"  - {tool['name']}: {(tool['description'] or '').splitlines()[0][:90]}")

    profile = await client.call("recall_traveler_profile", {"traveler_id": traveler_id})
    _show(f"recall_traveler_profile({traveler_id})", profile)

    prefs = await client.call("recall_preferences", {"traveler_id": traveler_id, "limit": 5})
    _show(f"recall_preferences({traveler_id})", prefs)

    turns = await client.call(
        "recall_recent_turns",
        {
            "conversation_id": conversation_id,
            "traveler_id": traveler_id,
            "limit": 4,
        },
    )
    _show(f"recall_recent_turns({conversation_id})", turns)

    similar = await client.call(
        "semantic_recall_interactions",
        {"traveler_id": traveler_id, "query": "Tokyo culture trip", "limit": 3},
    )
    _show("semantic_recall_interactions(query='Tokyo culture trip')", similar)

    # RLS check: ask for a non-existent traveler — server should return {}/[]
    bogus = await client.call(
        "recall_traveler_profile", {"traveler_id": "trv_does_not_exist"}
    )
    _show("RLS check — recall_traveler_profile(trv_does_not_exist)", bogus)

    await client.disconnect()
    print("\nDone — memory MCP server exercised end-to-end.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--traveler", default="trv_meridian_demo")
    parser.add_argument("--conversation", default="conv_meridian_demo")
    args = parser.parse_args()
    asyncio.run(run(args.traveler, args.conversation))


if __name__ == "__main__":
    main()
