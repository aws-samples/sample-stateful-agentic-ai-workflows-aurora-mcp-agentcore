#!/usr/bin/env python3
"""List the gateway's MCP tools and read one package through it, signed from this laptop.

This proves the gateway, the two targets and the read policies without placing
a hold: the laptop identity holds no traveler grant, and the point of the grant
is that only the MeridianHolds Lambda role does.

Usage:
    cd meridian
    python scripts/smoke_gateway_tools.py [PACKAGE_ID]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agentcore.gateway import get_agentcore_gateway  # noqa: E402

load_dotenv()
DETAILS_TOOL = "MeridianHolds___get_package_details"


def main() -> int:
    package_id = sys.argv[1] if len(sys.argv) > 1 else "CTY-002"
    gateway = get_agentcore_gateway()
    tools, _ = gateway.list_tools()
    print(f"{len(tools)} tools at {gateway.gateway_url}")
    for tool in tools:
        print(f"  {tool['name']}")
    raw = gateway.call_tool(DETAILS_TOOL, {"packageId": package_id})
    content = (raw.get("result") or {}).get("content") or []
    text = "".join(block.get("text", "") for block in content if isinstance(block, dict))
    if raw.get("error") or not text:
        print("tools/call failed:", json.dumps(raw)[:600])
        return 1
    payload = json.loads(text)
    if "error" in payload:
        print("tool error:", payload["error"])
        return 1
    package = payload["package"]
    print(f"{package['package_id']} · {package['name']} · availability {package.get('availability')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
