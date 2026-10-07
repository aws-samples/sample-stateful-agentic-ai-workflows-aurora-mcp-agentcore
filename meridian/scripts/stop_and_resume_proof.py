"""Prove a stopped AgentCore Runtime session resumes from Aurora on a new microVM.

Drives the local backend, which invokes the deployed MeridianWorkflow Runtime.

Mode ``waiting`` (default):
1. start the canonical recovery; it pauses for review with no hold;
2. stop the journey's Runtime session through the presenter endpoint;
3. resume; AgentCore starts a new microVM on the same session id, which claims
   the next attempt, restores the saved snapshot and places exactly one hold.

Mode ``running``:
1. start the canonical recovery; it pauses for review with no hold;
2. send the resume, then poll Aurora every 200 ms for the newest snapshot;
3. once its completed nodes include prepare_hold, stop the session mid-run;
4. the first resume fails with a 409 (lease lost) or a 503 (broken stream);
5. resume again; the new microVM reuses the saved hold request id.
If the run finishes before the stop lands, the script exits 2: that is not a pass.

Both modes read the journey document and Aurora back, then remove the run's rows.
Run from meridian/ with the backend on 127.0.0.1:8013 and AWS_PROFILE set.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from backend.agents.phase_05_workflow.graph import (  # noqa: E402
    fold_snapshot,
    snapshot_key,
)
from backend.db.rds_data_client import get_rds_data_client  # noqa: E402
from scripts.kill_and_resume_demo import (  # noqa: E402
    _holds_for,
    _purge,
    read_newest_snapshot,
)

API = os.getenv("MERIDIAN_PROOF_API", "http://127.0.0.1:8013/api")
TRAVELER = os.getenv("DEMO_TRAVELER_ID", "trv_meridian_demo")
CANONICAL = ("My JFK-to-Tokyo flight was canceled. Rework the trip, then check duration "
             "availability for the best three options.")
RESUME_MESSAGE = "Resume workflow from checkpoint"
POLL_SECONDS = 0.2
JOIN_SECONDS = 260
EXPECTED_FIRST_RESUME = {
    409: "the stopped worker reported a lost lease before its microVM ended",
    503: "the stream broke when the session stopped",
}
DEADLINE_SECONDS = 240
EXPECTED_STATUSES = {
    "waiting": ["paused", "succeeded"],
    "running": ["paused", "abandoned", "succeeded"],
}
MISSED_WINDOW = "missed the window: the run finished before the stop; run again"
NEWEST_COMPLETED_SQL = """
SELECT snapshot #> '{data,state,completed_nodes}' AS completed
  FROM workflow_snapshots WHERE session_id = %s AND storage_key = %s
 ORDER BY snapshot_seq DESC LIMIT 1
"""


class MissedWindow(Exception):
    """The run finished before the stop landed."""


def call(path: str, body: Optional[dict] = None) -> dict:
    """GET, or POST when a body is given, against the backend as JSON."""
    request = urllib.request.Request(
        API + path, data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method="POST" if body is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=240) as response:
        return json.loads(response.read())


def _executions(document: dict, count: int) -> list[dict]:
    items = (document.get("executions") or {}).get("items") or []
    return items[-count:]


def _check_executions(document: dict, during: str) -> list[str]:
    expected = EXPECTED_STATUSES[during]
    window = _executions(document, len(expected))
    statuses = [item.get("status") for item in window]
    failures = []
    if statuses != expected:
        failures.append(f"execution statuses are {statuses}, expected {expected}")
    if len(window) < 2:
        return failures
    sessions = {item.get("runtime_session_id") for item in window}
    if len(sessions) != 1:
        failures.append(f"resume ran in a different session: {sorted(map(str, sessions))}")
    final = window[-1].get("microvm_id")
    earlier = {item.get("microvm_id") for item in window[:-1]}
    if final in earlier:
        failures.append(f"resume ran on the same microVM {final}")
    return failures


def _check_workflow(document: dict) -> list[str]:
    workflow = document.get("workflow") or {}
    failures = []
    if workflow.get("workflow_status") != "resumed":
        failures.append(
            f"workflow_status is {workflow.get('workflow_status')!r}, expected 'resumed'")
    if workflow.get("resumed_after_restart") is not True:
        failures.append(f"resumed_after_restart is {workflow.get('resumed_after_restart')!r}, "
                        "expected True")
    return failures


def _check_stop(document: dict, during: str) -> list[str]:
    stops = document.get("session_stops") or {}
    items = stops.get("items") or []
    if stops.get("status") != "observed" or not items or items[0].get("outcome") != "stopped":
        return [f"no stop was recorded: status={stops.get('status')!r} rows={len(items)}"]
    failures = []
    if items[0].get("stopped_during") != during:
        failures.append(
            f"stopped_during is {items[0].get('stopped_during')!r}, expected {during!r}")
    if during == "running" and not items[0].get("last_step"):
        failures.append("the running stop recorded no last_step")
    return failures


def _check_holds(holds: list[dict], during: str, saved: Optional[str]) -> list[str]:
    held = [h for h in holds if h.get("status") == "held"]
    if len(holds) != 1 or len(held) != 1:
        return [f"expected one held hold, found {len(holds)} holds and {len(held)} held"]
    if (during == "running" or saved is not None) and held[0].get("hold_request_id") != saved:
        return [f"hold_request_id is {held[0].get('hold_request_id')!r}, "
                f"the saved intent had {saved!r}"]
    return []


def check_restart(
    document: dict, holds: list[dict], during: str = "waiting",
    saved_hold_request_id: Optional[str] = None,
) -> list[str]:
    """Return one failure text per failed check; an empty list means the proof holds.

    Args:
        document: The journey document read after the resume.
        holds: The journey's holds as Aurora holds them.
        during: ``waiting`` or ``running``, the moment the stop is expected to catch.
        saved_hold_request_id: The hold request id captured from the saved snapshot's
            hold intent before the resume; the hold must carry it. Required in ``running`` mode.

    Returns:
        The failed checks, each naming its numbers.
    """
    return [
        *_check_executions(document, during),
        *_check_workflow(document),
        *_check_stop(document, during),
        *_check_holds(holds, during, saved_hold_request_id),
    ]


def _intent_id(snapshot: Optional[dict]) -> Optional[str]:
    intent = fold_snapshot(snapshot).get("hold_intent") if snapshot else None
    return (intent or {}).get("hold_request_id")


async def _journey_id(thread: str) -> str:
    listing = await asyncio.to_thread(call, f"/journeys?thread_id={thread}")
    journeys = listing.get("journeys") or []
    if not journeys:
        raise RuntimeError(f"no journey found for thread {thread}")
    return journeys[0]["journey_id"]


def _resume_body(thread: str) -> dict:
    return {"message": RESUME_MESSAGE, "phase": 5, "customer_id": TRAVELER,
            "conversation_id": thread, "resume": True, "travelers_count": 2}


async def _start(client, thread: str) -> str:
    reply = await asyncio.to_thread(call, "/chat", {
        "message": CANONICAL, "phase": 5, "customer_id": TRAVELER,
        "conversation_id": thread, "travelers_count": 2,
    })
    journey = await _journey_id(thread)
    holds = await _holds_for(client, journey)
    status = reply.get("workflow_status")
    print(f"start: workflow_status={status} holds={len(holds)} journey={journey}")
    if status != "paused" or holds:
        raise RuntimeError(f"expected a paused run with no hold, got {status!r} and {len(holds)}")
    return journey


async def _stop(journey: str) -> dict:
    stop = await asyncio.to_thread(call, f"/journeys/{journey}/stop-session", {})
    print(f"stop: outcome={stop.get('outcome')} stopped_during={stop.get('stopped_during')} "
          f"last_step={stop.get('last_step')} session={stop.get('runtime_session_id')}")
    if stop.get("outcome") != "stopped":
        raise RuntimeError(f"the stop outcome was {stop.get('outcome')!r}")
    return stop


async def _print_holds(client, journey: str) -> None:
    holds = await _holds_for(client, journey)
    print(f"holds_just_after_stop (approximate, read after the stop returned): {len(holds)}")


async def _missed(client, journey: str) -> MissedWindow:
    await _print_holds(client, journey)
    return MissedWindow(MISSED_WINDOW)


async def _newest_completed(client, thread: str) -> list:
    rows = await client.execute(NEWEST_COMPLETED_SQL, (thread, snapshot_key(thread)))
    return json.loads(rows[0]["completed"]) if rows and rows[0]["completed"] else []


async def _stop_when_prepare_hold_is_saved(client, journey: str, thread: str, resume) -> dict:
    """Poll Aurora as master until the newest snapshot holds prepare_hold, then stop."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + DEADLINE_SECONDS
    while loop.time() < deadline:
        if "prepare_hold" in await _newest_completed(client, thread):
            break
        if resume.done():
            if resume.exception() is not None:
                raise resume.exception()
            raise await _missed(client, journey)
        await asyncio.sleep(POLL_SECONDS)
    else:
        raise RuntimeError("prepare_hold never appeared in the newest snapshot")
    try:
        stop = await _stop(journey)
    except urllib.error.HTTPError as exc:
        if exc.code == 409:
            raise await _missed(client, journey) from exc
        raise
    await _print_holds(client, journey)
    if stop.get("stopped_during") != "running":
        raise await _missed(client, journey)
    print(f"caught: last_step={stop.get('last_step')}")
    return stop


async def _first_resume_result(resume) -> None:
    try:
        await resume
    except urllib.error.HTTPError as exc:
        if exc.code not in EXPECTED_FIRST_RESUME:
            raise RuntimeError(
                f"first resume returned HTTP {exc.code}, expected 409 or 503") from exc
        print(f"first resume: HTTP {exc.code}, {EXPECTED_FIRST_RESUME[exc.code]}")
        return
    raise MissedWindow(MISSED_WINDOW)


async def _join(resume) -> None:
    """Wait for the background resume so nothing runs while the rows are purged."""
    done, _pending = await asyncio.wait({resume}, timeout=JOIN_SECONDS)
    for future in done:
        future.exception()
    if not done:
        print(f"warning: the background resume was still running after {JOIN_SECONDS} s")


async def _interrupted_resume(client, journey: str, thread: str) -> Optional[str]:
    """Stop mid-run and return the hold request id the snapshot had saved at that moment."""
    resume = asyncio.ensure_future(asyncio.to_thread(call, "/chat", _resume_body(thread)))
    try:
        await _stop_when_prepare_hold_is_saved(client, journey, thread, resume)
        captured = _intent_id(await read_newest_snapshot(client, thread))
        await _first_resume_result(resume)
        return captured
    finally:
        await _join(resume)


async def _drive(client, thread: str, during: str) -> tuple[str, Optional[str], str]:
    journey = await _start(client, thread)
    if during == "waiting":
        captured = _intent_id(await read_newest_snapshot(client, thread))
        await _stop(journey)
        where = "the paused snapshot"
    else:
        captured = await _interrupted_resume(client, journey, thread)
        where = "the snapshot right after the stop"
    reply = await asyncio.to_thread(call, "/chat", _resume_body(thread))
    print(f"resume: workflow_status={reply.get('workflow_status')}")
    if captured is not None:
        return journey, captured, f"captured from {where}"
    after = _intent_id(await read_newest_snapshot(client, thread))
    return journey, after, f"no intent existed in {where}; compared against the post-resume intent"


def _print_evidence(document: dict, holds: list[dict], expected: Optional[str], case: str) -> None:
    items = (document.get("executions") or {}).get("items") or []
    for item in items[-3:]:
        print(f"execution: status={item.get('status')} session={item.get('runtime_session_id')} "
              f"microvm={item.get('microvm_id')}")
    stops = (document.get("session_stops") or {}).get("items") or []
    if stops:
        print(f"stop row: outcome={stops[0].get('outcome')} "
              f"stopped_during={stops[0].get('stopped_during')} "
              f"last_step={stops[0].get('last_step')}")
    for hold in holds:
        print(f"hold: booking={hold.get('booking_id')} status={hold.get('status')} "
              f"hold_request_id={hold.get('hold_request_id')} "
              f"matches_saved_intent={hold.get('hold_request_id') == expected} ({case})")
    print(f"resumed_after_restart={(document.get('workflow') or {}).get('resumed_after_restart')}")


async def _purge_by_thread(client, thread: str) -> None:
    for table, column in (("workflow_snapshots", "session_id"),
                          ("journey_executions", "thread_id")):
        await client.execute(f"DELETE FROM {table} WHERE {column} = %s", (thread,))


async def _purge_run(client, thread: str) -> None:
    rows = await client.execute("SELECT journey_id FROM journey_threads WHERE thread_id = %s",
                                (thread,))
    if not rows:
        await _purge_by_thread(client, thread)
        print(f"cleanup: no journey row for {thread}; purged its snapshots and executions")
        return
    journey = rows[0]["journey_id"]
    await _purge(client, journey, thread)
    left = await client.execute(
        "SELECT COUNT(*) AS n FROM workflow_snapshots WHERE session_id = %s", (thread,))
    print(f"cleanup: purged {journey}; holds left={len(await _holds_for(client, journey))} "
          f"snapshots left={left[0]['n']}")


async def _cleanup(client, thread: str) -> None:
    """Purge the run; a purge failure must not hide the error already in flight."""
    in_flight = sys.exc_info()[1] is not None
    try:
        await _purge_run(client, thread)
    except Exception as exc:  # noqa: BLE001 - reported below, re-raised when nothing else failed
        print(f"cleanup FAILED for {thread}: {exc}", file=sys.stderr)
        if not in_flight:
            raise


async def run(during: str, keep: bool) -> int:
    """Run one proof in the given mode and return its exit code (0, 1 or 2)."""
    client = get_rds_data_client()
    thread = f"phase5-proof-{uuid.uuid4().hex[:8]}"
    try:
        journey, expected, case = await _drive(client, thread, during)
        document = await asyncio.to_thread(call, f"/journeys/{journey}")
        holds = await _holds_for(client, journey)
        failures = check_restart(document, holds, during, saved_hold_request_id=expected)
        _print_evidence(document, holds, expected, case)
    except MissedWindow as exc:
        print(f"RESULT: {exc}")
        return 2
    finally:
        if not keep:
            await _cleanup(client, thread)
    for line in failures:
        print(f"[BAD] {line}")
    print(f"RESULT: {'PASS' if not failures else 'FAIL'} ({during})")
    return 0 if not failures else 1


def main(argv: Optional[list[str]] = None) -> int:
    """Parse arguments and run the proof."""
    parser = argparse.ArgumentParser(description="Prove a stopped Runtime session resumes.")
    parser.add_argument("--during", choices=sorted(EXPECTED_STATUSES), default="waiting",
                        help="stop the session while it waits for review or while it runs")
    parser.add_argument("--keep", action="store_true", help="leave the run's rows behind")
    args = parser.parse_args(argv)
    return asyncio.run(run(args.during, args.keep))


if __name__ == "__main__":
    raise SystemExit(main())
