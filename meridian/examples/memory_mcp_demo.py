"""
Stand-alone smoke test / demo for the custom Meridian memory MCP server.

What it does
============

Spins up `backend.mcp.memory_server` over stdio, lists the tools it
exposes, then exercises each one against the real Aurora cluster
(via RDS Data API).  Run it to prove that:

    1. the custom MCP server actually starts up,
    2. it speaks Model Context Protocol like any other server, and
    3. its tools authorize the workload for a traveler before Aurora RLS.

Usage
=====

From `meridian/`:

    source venv/bin/activate
    PYTHONPATH=. python examples/memory_mcp_demo.py

Required env (same as Phase 4):

    AURORA_CLUSTER_ARN, AURORA_SECRET_ARN, AWS_DEFAULT_REGION

The defaults are the seeded traveler `trv_meridian_demo` and its seeded Tokyo conversation
`conv_tokyo_october` (``scripts/travel_catalog.py``), so the recent-turns recall prints turns.
Pass `--traveler` and `--conversation` to target other data.

The example exits 0 only when every read succeeds and the negative control gets the expected
refusal (the workload has no binding for `trv_demo_decoy`). A database or transport error during
that call fails the run, and so does an answer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from dotenv import load_dotenv

from backend.mcp.memory_mcp_client import MeridianMemoryMCPClient, TravelerNotAuthorized
from backend.security.redaction import mask_secrets

load_dotenv()


def _show(label: str, value: Any) -> None:
    print(f"\n--- {label} ---")
    print(json.dumps(value, indent=2, default=str)[:1500])


DECOY_TRAVELER = "trv_demo_decoy"
DEFAULT_CONVERSATION = "conv_tokyo_october"


async def authorization_negative_control(client: MeridianMemoryMCPClient) -> None:
    """Ask for the decoy's profile; only the traveler-binding denial counts as a pass.

    This workload has no binding to the decoy, so the MCP server rejects the claim before it
    sets an RLS scope. A database or transport error is a failure, not a denial, and so is an
    answer.

    Raises:
        SystemExit: With code 1 when the call is allowed or fails for any other reason.
    """
    try:
        await client.call("recall_traveler_profile", {"traveler_id": DECOY_TRAVELER})
    except TravelerNotAuthorized as exc:
        if exc.traveler_id != DECOY_TRAVELER:
            _show("Authorization negative control: denial names another traveler",
                  {"decision": "error", "error": mask_secrets(str(exc))[:300]})
            raise SystemExit(1) from exc
        _show(f"Authorization negative control: DENY {DECOY_TRAVELER}",
              {"decision": "deny", "error": mask_secrets(str(exc))[:300]})
    except Exception as exc:  # noqa: BLE001 - any other failure must fail the example
        _show("Authorization negative control: FAILED, not a denial",
              {"decision": "error",
               "error": f"{type(exc).__name__}: {mask_secrets(str(exc))[:300]}"})
        raise SystemExit(1) from exc
    else:
        _show("Authorization negative control", {"unexpected": "allowed"})
        raise SystemExit(1)


async def run(traveler_id: str, conversation_id: str) -> None:
    client = MeridianMemoryMCPClient()
    print("Launching meridian-memory MCP server (stdio) ...")
    await client.connect()
    try:
        await exercise(client, traveler_id, conversation_id)
        await authorization_negative_control(client)
    finally:
        await client.disconnect()
    print("\nDone: memory MCP server exercised end-to-end.")


async def exercise(client: MeridianMemoryMCPClient, traveler_id: str, conversation_id: str) -> None:
    """Call each read tool for the traveler and print what came back."""
    print("\nTools exposed by the server:")
    for tool in client.tools:
        print(f"  - {tool['name']}: {(tool['description'] or '').splitlines()[0][:90]}")

    profile = await client.call("recall_traveler_profile", {"traveler_id": traveler_id})
    _show(f"recall_traveler_profile({traveler_id})", profile)

    prefs = await client.call("recall_preferences", {"traveler_id": traveler_id, "limit": 5})
    _show(f"recall_preferences({traveler_id})", prefs)

    turns = await client.call(
        "recall_recent_turns",
        {"conversation_id": conversation_id, "traveler_id": traveler_id, "limit": 4},
    )
    _show(f"recall_recent_turns({conversation_id})", turns)

    similar = await client.call(
        "semantic_recall_interactions",
        {"traveler_id": traveler_id, "query": "Tokyo culture trip", "limit": 3},
    )
    _show("semantic_recall_interactions(query='Tokyo culture trip')", similar)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--traveler", default="trv_meridian_demo")
    parser.add_argument("--conversation", default=DEFAULT_CONVERSATION)
    args = parser.parse_args()
    asyncio.run(run(args.traveler, args.conversation))


if __name__ == "__main__":
    main()
