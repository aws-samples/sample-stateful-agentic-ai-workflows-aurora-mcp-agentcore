"""Warm every service the demo touches so the first live turn on stage is not cold.

Runs, in order: health (Aurora), the catalog, the traveler profile under RLS, one
read-only turn each for SQL, MCP, Retrieval and Production (AgentCore Runtime,
Memory and Gateway), and the saved-journey list. It never places a hold, confirms
a booking or starts a recovery workflow.

Local (default): python scripts/warm_demo.py
Hosted:          MERIDIAN_HOSTED_AUTH='{"username": ..., "password": ...}' \
                 python scripts/warm_demo.py --hosted
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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.demo_prompts import PROMPT_LADDER  # noqa: E402
from published import load_record, release_url  # noqa: E402

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


def steps() -> list[tuple[str, str, dict | None, object]]:
    """The warm-up sequence: label, path, request body, result check."""
    def turn(phase: int, prompt: str) -> dict:
        return {"message": prompt, "phase": phase, "customer_id": TRAVELER_ID}

    return [
        ("Health and Aurora", "/api/health", None, check_health),
        ("Catalog", "/api/products?limit=50", None, None),
        ("Traveler profile (RLS)", f"/api/memory/{TRAVELER_ID}", None, None),
        ("Phase 1 SQL", "/api/chat", turn(1, PROMPT_LADDER[1].works[0]), check_turn),
        ("Phase 2 MCP", "/api/chat", turn(2, PROMPT_LADDER[2].works[0]), check_turn),
        ("Phase 3 Retrieval", "/api/chat", turn(3, PROMPT_LADDER[3].works[0]), check_turn),
        ("Phase 4 Production", "/api/chat", turn(4, PROMPT_LADDER[4].works[1]), check_turn),
        ("Saved journeys", "/api/journeys?limit=5", None, None),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--hosted", action="store_true",
                        help="warm the hosted site from the local release record")
    args = parser.parse_args()
    base = release_url(load_record()).rstrip("/") if args.hosted else LOCAL_URL
    headers = basic_auth_header() if args.hosted else {}
    print(f"Warming {base}")
    failures = 0
    for label, path, body, check in steps():
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
