"""Warm every service the demo touches so the first live turn on stage is not cold.

Runs, in order: health (Aurora), the catalog, the traveler profile under RLS, one
read-only turn each for SQL, MCP, Retrieval and Production (AgentCore Runtime,
Memory and Gateway), and the saved-journey list. It never places a hold, confirms
a booking or starts a recovery workflow.

Local (default): python scripts/warm_demo.py
Hosted:          MERIDIAN_HOSTED_AUTH='{"username": ..., "password": ...}' \
                 python scripts/warm_demo.py --hosted

With MERIDIAN_AGENTCORE_AUTH=jwt the script signs in as a seeded user (``--as jordan`` or
``--as decoy``) and sends that access token; the edge credential is not used.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.demo_prompts import PROMPT_LADDER  # noqa: E402
from scripts.agentcore_caller import (  # noqa: E402
    TRAVELER_USERS, bearer_headers, require_token_safe_url, traveler_for_user,
)
from scripts.published import load_record, release_url  # noqa: E402

LOCAL_URL = "http://127.0.0.1:8013"
TRAVELER_ID = "trv_meridian_demo"
TURN_TIMEOUT_SECONDS = 120
READ_TIMEOUT_SECONDS = 30


def basic_auth_header() -> dict[str, str]:
    """Build the hosted site's sign-in header from MERIDIAN_HOSTED_AUTH."""
    raw = os.environ.get("MERIDIAN_HOSTED_AUTH")
    if not raw:
        raise SystemExit(
            "--hosted needs MERIDIAN_HOSTED_AUTH as JSON with username and password."
        )
    try:
        auth = json.loads(raw)
        token = f"{auth['username']}:{auth['password']}".encode()
    except (ValueError, KeyError) as error:
        raise SystemExit("MERIDIAN_HOSTED_AUTH must contain username and password") from error
    return {"Authorization": "Basic " + base64.b64encode(token).decode()}


def request_headers(hosted: bool, user: str) -> dict[str, str]:
    """The seeded user's token in jwt mode; otherwise the edge credential for a hosted site."""
    token = bearer_headers(user)
    if token:
        return token
    return basic_auth_header() if hosted else {}


def call(base: str, headers: dict[str, str], path: str, body: dict | None = None) -> object:
    """Send one request and return its JSON body; raise on any HTTP or network error."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        base + path,
        data=data,
        headers={**headers, **({"Content-Type": "application/json"} if data else {})},
        method="POST" if data else "GET",
    )
    timeout = TURN_TIMEOUT_SECONDS if data else READ_TIMEOUT_SECONDS
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read() or b"{}")


def check_health(result: dict) -> str | None:
    if result.get("status") != "healthy" or not result.get("aurora_reachable"):
        return f"not healthy: {result.get('degraded_component') or result.get('status')}"
    return None


def check_turn(result: dict) -> str | None:
    failed = [
        a.get("title", "?") for a in result.get("activities", [])
        if (a.get("telemetry") or {}).get("status") in {"error", "failed"}
    ]
    if failed:
        return "failed steps: " + ", ".join(failed)
    return None if result.get("message") else "empty reply"


def check_catalog(result: dict) -> str | None:
    """The catalog must list at least one package."""
    packages = result.get("packages")
    return None if isinstance(packages, list) and packages else "the catalog is empty"


def check_profile(result: dict, traveler: str = TRAVELER_ID) -> str | None:
    """The profile read under RLS must be this traveler's and carry a facts list."""
    if result.get("traveler_id") != traveler:
        return f"the profile is for {result.get('traveler_id')!r}, not {traveler}"
    return None if isinstance(result.get("facts"), list) else "the profile has no facts list"


def check_journeys(result: dict) -> str | None:
    """The saved-journey list must be a list, empty or not."""
    return None if isinstance(result.get("journeys"), list) else "no journeys list in the reply"


def steps(traveler: str = TRAVELER_ID) -> list[tuple[str, str, dict | None, object]]:
    """The warm-up sequence for ``traveler``: label, path, request body, result check."""
    def turn(phase: int, prompt: str) -> dict:
        return {"message": prompt, "phase": phase, "customer_id": traveler}

    return [
        ("Health and Aurora", "/api/health", None, check_health),
        ("Catalog", "/api/products?limit=50", None, check_catalog),
        ("Traveler profile (RLS)", f"/api/memory/{traveler}", None,
         partial(check_profile, traveler=traveler)),
        ("Phase 1 SQL", "/api/chat", turn(1, PROMPT_LADDER[1].works[0]), check_turn),
        ("Phase 2 MCP", "/api/chat", turn(2, PROMPT_LADDER[2].works[0]), check_turn),
        ("Phase 3 Retrieval", "/api/chat", turn(3, PROMPT_LADDER[3].works[0]), check_turn),
        ("Phase 4 Production", "/api/chat", turn(4, PROMPT_LADDER[4].works[1]), check_turn),
        ("Saved journeys", "/api/journeys?limit=5", None, check_journeys),
    ]


def build_parser() -> argparse.ArgumentParser:
    """The command line: the target (local or hosted) and the user to sign in as."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--hosted", action="store_true",
                        help="warm the hosted site from the local release record")
    target.add_argument("--base-url", default=LOCAL_URL,
                        help=f"warm this backend instead of {LOCAL_URL} (a credential goes only "
                             "to https, or http on localhost)")
    parser.add_argument("--as", dest="as_user", choices=sorted(set(TRAVELER_USERS.values())),
                        default="jordan", help="the seeded user to sign in as in jwt mode")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    base = release_url(load_record()).rstrip("/") if args.hosted else args.base_url.rstrip("/")
    require_token_safe_url(base, always=args.hosted)
    headers = request_headers(args.hosted, args.as_user)
    print(f"Warming {base}")
    failures = 0
    for label, path, body, check in steps(traveler_for_user(args.as_user)):
        started = time.monotonic()
        try:
            problem = check(call(base, headers, path, body)) if check else None
            call_ok = True
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            problem, call_ok = f"{type(error).__name__}: {error}", False
        elapsed = time.monotonic() - started
        failures += bool(problem)
        print(f"  {'FAIL' if problem else 'ok  '}  {label:<24} {elapsed:5.1f}s"
              + (f"  {problem}" if problem else ""))
        if not call_ok and path == "/api/health":
            print("  Stopping: the backend did not answer its health check.")
            return 1
    print("Warm." if not failures else f"{failures} step(s) need attention before going live.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
