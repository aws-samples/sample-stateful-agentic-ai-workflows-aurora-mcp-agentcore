"""Warm every service the demo touches so the first live turn is not cold.

Runs, in order: health (Aurora), the catalog, the traveler profile under RLS, one
chat turn each for SQL, MCP, Retrieval and Production (AgentCore Runtime, Memory and
Gateway), and the saved-journey list. It never places a hold, confirms a booking or
starts a recovery workflow.

The four chat turns are NOT read-only, and the warm-up is not a health probe. Each turn
can write conversation, message and trip-interaction rows and traveler preferences for
the seeded traveler, and every authorization check adds append-only audit rows
(``traveler_access_audit`` and the agent audit log). Nothing is deleted afterwards.
``--conversation-ids-file`` records the conversation id of each turn so you can find its
rows. A health probe (``/health``, or ``/api/health`` for the Aurora check) only reads.

Local (default): python scripts/warm_demo.py
Hosted:          MERIDIAN_HOSTED_AUTH='{"username": ..., "password": ...}' \
                 python scripts/warm_demo.py --hosted

Verify:          add --verify to --hosted. A full pass writes status "verified" to the release
                 record (time, commit, check names); a failing step writes "verification_failed"
                 with that step's name and exits 1.

With MERIDIAN_AGENTCORE_AUTH=jwt the script signs in as a seeded user (``--as jordan`` or
``--as decoy``) and sends that access token; the edge credential is not used.

Direct backend URL in the ``iam`` release (``--base-url <ServiceEndpoint>``): the backend wants
the shared API token as a bearer. Set ``MERIDIAN_API_TOKEN`` for that one command, resolved from
the Secrets Manager secret ``meridian/web/api-token`` and never written to a file; it goes only
to an https URL or to localhost.
"""

from __future__ import annotations

import argparse
import base64
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping
from functools import partial
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.prompt_ladder import PROMPT_LADDER  # noqa: E402
from scripts.agentcore_caller import (  # noqa: E402
    TRAVELER_USERS, bearer_headers, require_token_safe_url, traveler_for_user,
)
from scripts.published import load_record, record_verification, release_url  # noqa: E402

load_dotenv()

LOCAL_URL = "http://127.0.0.1:8013"
TRAVELER_ID = "trv_meridian_demo"
TURN_TIMEOUT_SECONDS = 120
READ_TIMEOUT_SECONDS = 30
DEFAULT_USER = "jordan"


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


def shared_token_header(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The shared API token as a bearer header, when ``MERIDIAN_API_TOKEN`` holds a real one."""
    token = (os.environ if environ is None else environ).get("MERIDIAN_API_TOKEN", "")
    if not token or token.startswith("{{resolve:"):
        return {}
    return {"Authorization": f"Bearer {token}"}


def request_headers(hosted: bool, user: str) -> dict[str, str]:
    """The seeded user's token in jwt mode; else the hosted edge credential or the shared token."""
    token = bearer_headers(user)
    if token:
        return token
    return basic_auth_header() if hosted else shared_token_header()


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


NOT_AN_OBJECT = "the reply is not a JSON object"


def check_health(result: dict) -> str | None:
    if not isinstance(result, dict):
        return NOT_AN_OBJECT
    if result.get("status") != "healthy" or not result.get("aurora_reachable"):
        return f"not healthy: {result.get('degraded_component') or result.get('status')}"
    return None


def check_turn(result: dict) -> str | None:
    """Fail a chat turn when a step failed or the route fell back to its error reply.

    The backend answers HTTP 200 with an "I encountered an error" message and an activity of
    type ``error`` when a route hits its catch-all, so the status alone does not prove success.
    """
    if not isinstance(result, dict):
        return NOT_AN_OBJECT
    activities = result.get("activities", [])
    if not isinstance(activities, list) or not all(isinstance(a, dict) for a in activities):
        return "the activities field is not a list of objects"
    failed = [
        a.get("title", "?") for a in activities
        if a.get("activity_type") == "error"
        or (a.get("telemetry") or {}).get("status") in {"error", "failed"}
    ]
    if failed:
        return "failed steps: " + ", ".join(failed)
    return None if result.get("message") else "empty reply"


def check_catalog(result: dict) -> str | None:
    """The catalog must list at least one product."""
    if not isinstance(result, dict):
        return NOT_AN_OBJECT
    products = result.get("products")
    return None if isinstance(products, list) and products else "the catalog is empty"


def check_profile(result: dict, traveler: str = TRAVELER_ID) -> str | None:
    """The profile read under RLS must be this traveler's and carry a facts list."""
    if not isinstance(result, dict):
        return NOT_AN_OBJECT
    if result.get("traveler_id") != traveler:
        return f"the profile is for {result.get('traveler_id')!r}, not {traveler}"
    return None if isinstance(result.get("facts"), list) else "the profile has no facts list"


def check_journeys(result: dict) -> str | None:
    """The saved-journey list must be a list, empty or not."""
    if not isinstance(result, dict):
        return NOT_AN_OBJECT
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
    parser.add_argument("--verify", action="store_true",
                        help="with --hosted, record the result in the release record")
    parser.add_argument("--conversation-ids-file", type=Path, default=None,
                        help="give each chat turn its own conversation id and record the ids in "
                             "this file, one per line, written before the turn is sent")
    parser.add_argument("--as", dest="as_user", choices=sorted(set(TRAVELER_USERS.values())),
                        default=DEFAULT_USER, help="the seeded user to sign in as in jwt mode")
    return parser


def with_recorded_conversation(path: str, body: dict | None, ids_file: Path | None) -> dict | None:
    """Give a chat turn its own conversation id and record the id in ``ids_file`` first.

    The id is written before the turn is sent, so a caller can tell which rows the turn left
    behind even when the turn timed out. Without ``ids_file`` the body is returned unchanged.
    """
    if ids_file is None or path != "/api/chat" or body is None:
        return body
    conversation_id = f"wrm_{uuid.uuid4().hex[:12]}"
    with ids_file.open("a", encoding="utf-8") as handle:
        handle.write(conversation_id + "\n")
    return {**body, "conversation_id": conversation_id}


def run_steps(
    base: str, headers: dict[str, str], traveler: str, conversation_ids_file: Path | None = None,
) -> tuple[list[str], list[str]]:
    """Run the warm-up; return the labels that passed and the labels that failed, in order."""
    passed: list[str] = []
    failed: list[str] = []
    for label, path, body, check in steps(traveler):
        body = with_recorded_conversation(path, body, conversation_ids_file)
        started = time.monotonic()
        try:
            problem = check(call(base, headers, path, body)) if check else None
            call_ok = True
        except (OSError, http.client.HTTPException, ValueError) as error:
            problem, call_ok = f"{type(error).__name__}: {error}", False
        elapsed = time.monotonic() - started
        (failed if problem else passed).append(label)
        print(f"  {'FAIL' if problem else 'ok  '}  {label:<24} {elapsed:5.1f}s"
              + (f"  {problem}" if problem else ""))
        if not call_ok and path == "/api/health":
            print("  Stopping: the backend did not answer its health check.")
            break
    return passed, failed


def main(argv: list[str] | None = None) -> int:
    """Warm the target. Exit 0 when every step passes, 1 on a failure, 2 on a usage error."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.verify and not args.hosted:
        parser.error("--verify records a result for the hosted release; add --hosted")
    if args.verify and args.as_user != DEFAULT_USER:
        parser.error(f"--verify records the release verdict for {DEFAULT_USER} only; "
                     f"--as {args.as_user} would check a different traveler and write nothing")
    base = release_url(load_record()).rstrip("/") if args.hosted else args.base_url.rstrip("/")
    require_token_safe_url(base, always=args.hosted or bool(shared_token_header()))
    headers = request_headers(args.hosted, args.as_user)
    print(f"Warming {base}")
    if args.conversation_ids_file:
        args.conversation_ids_file.write_text("", encoding="utf-8")
    passed, failed = run_steps(
        base, headers, traveler_for_user(args.as_user), args.conversation_ids_file)
    if args.verify:
        record = record_verification(passed, failed[0] if failed else None)
        print(f"Release record: {record['status']}.")
    if failed:
        print(f"{len(failed)} step(s) need attention before going live.")
    else:
        print("Warm.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
