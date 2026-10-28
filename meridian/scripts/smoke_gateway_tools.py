#!/usr/bin/env python3
"""List the gateway's MCP tools and read one package through it, signed from this laptop.

This proves the gateway, the targets and the read policies without placing a hold: the laptop
identity holds no traveler grant, and the point of the grant is that only the MeridianHolds
Lambda role does. With --probe-release it also calls release_hold for a hold that does not
exist, which changes nothing and tells a missing policy from a working tool.

Usage:
    cd meridian
    venv/bin/python scripts/smoke_gateway_tools.py [PACKAGE_ID] [--probe-release]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agentcore.gateway import get_agentcore_gateway  # noqa: E402
from backend.agents.phase_05_workflow.governed_hold import (  # noqa: E402
    RELEASE_TOOL,
    UNREADABLE_RESULT,
    envelope_error,
    envelope_failure,
    log_detail,
    release_arguments,
    release_governed_hold,
    response_text,
    scrub_detail,
    target_error,
)
from backend.agents.phase_05_workflow.governed_tools import GatewayToolError  # noqa: E402
from scripts.agentcore_caller import caller_scope  # noqa: E402

load_dotenv()
DETAILS_TOOL = "MeridianHolds___get_package_details"
PROBE_TRAVELER = "trv_meridian_demo"


def classify_release_probe(raw: dict) -> tuple[bool, str]:
    """Judge the answer to a release of a hold that does not exist.

    Returns:
        ``(passed, message)``. Only ``hold_not_found`` passes: the call got through the
        interceptor, Cedar and the Lambda's grant, and found nothing to release. The message
        holds fixed words only, never text the Gateway sent.
    """
    outcome = release_governed_hold(lambda *_: raw, {})
    if outcome.error == "hold_not_found":
        return True, "release_hold reached the Lambda and found no such hold"
    if outcome.refused_by == "policy":
        return False, (
            "Cedar denied release_hold by default: deploy the meridian_release_governance policy"
        )
    if outcome.refused_by == "identity_check":
        return False, f"the interceptor refused the call: {outcome.error}"
    if outcome.released:
        return False, "release_hold released a hold that should not exist"
    return False, f"unexpected answer: {outcome.error}"


def probe_release(gateway) -> bool:
    """Call release_hold for an unknown hold and print what the probe shows."""
    print(
        f"probing release_hold; travelerId sent: {PROBE_TRAVELER} (in jwt mode the interceptor "
        "pins it to the traveler of the caller's token, the seeded user jordan by default)"
    )
    arguments = release_arguments(
        traveler_id=PROBE_TRAVELER,
        hold_request_id="hrq_smoke_unknown",
        journey_id="jrn_smoke_unknown",
    )
    passed, message = classify_release_probe(gateway.call_tool(RELEASE_TOOL, arguments))
    print(("ok: " if passed else "FAILED: ") + message)
    return passed


def read_package(gateway, package_id: str) -> tuple[bool, str]:
    """Read one package through the Gateway; the line is fixed words or catalog fields.

    Returns:
        ``(ok, line)``. Nothing the Gateway or a target sent as an error is returned.
    """
    raw = gateway.call_tool(DETAILS_TOOL, {"packageId": package_id})
    text = response_text(raw)
    failure = envelope_failure(raw, text)
    if failure is not None:
        message, refused_by = failure
        log_detail(DETAILS_TOOL, message)
        return False, f"tools/call failed: {envelope_error(refused_by, message)}"
    try:
        payload = json.loads(text)
    except ValueError:
        return False, f"tools/call failed: {UNREADABLE_RESULT}"
    if not isinstance(payload, dict):
        return False, f"tools/call failed: {UNREADABLE_RESULT}"
    if payload.get("error") is not None:
        log_detail(DETAILS_TOOL, str(payload["error"]))
        return False, f"tool error: {target_error(payload['error'])}"
    package = payload.get("package")
    if not isinstance(package, dict):
        return False, f"tools/call failed: {UNREADABLE_RESULT}"
    return True, (
        f"{package.get('package_id')}, {package.get('name')}, "
        f"availability {package.get('availability')}"
    )


def run(gateway, args: argparse.Namespace) -> int:
    """List the tools, read the package, and probe the release when asked."""
    with caller_scope():
        tools, _ = gateway.list_tools()
        print(f"{len(tools)} tools at {gateway.gateway_url}")
        for tool in tools:
            print(f"  {tool['name']}")
        ok, line = read_package(gateway, args.package_id)
        probed = probe_release(gateway) if args.probe_release else True
    print(line)
    return 0 if ok and probed else 1


def main(argv: Optional[list[str]] = None, gateway=None) -> int:
    """Run the smoke checks; a Gateway failure prints a fixed line and returns 1."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("package_id", nargs="?", default="CTY-002")
    parser.add_argument(
        "--probe-release",
        action="store_true",
        help="also call release_hold for a hold that does not exist",
    )
    args = parser.parse_args(argv)
    try:
        return run(gateway or get_agentcore_gateway(), args)
    except GatewayToolError as error:
        print(f"gateway call failed ({error.code}): {error}")
    except (RuntimeError, OSError, ValueError) as error:
        print(f"gateway call failed ({type(error).__name__}): {scrub_detail(str(error))}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
