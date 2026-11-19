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

Both modes read the journey document and Aurora back, then give back the run's hold through the
governed Gateway release tool (as the traveler, in ``jwt`` mode with the traveler's token). Nothing
is deleted: the run's journey, hold request, booking and their action audit rows stay. Only the
holds of this run's own thread are touched, so an older ``phase5-proof-`` thread is left alone.
A hold is read back before each release call, a second call goes out only after a read-back
shows it still held, and each hold gets at most two calls.

``--thread`` names the run's thread (the backend-login proof passes one, so it still knows the id
if this process dies). ``--release-only --thread T`` only gives back T's active holds.
``--calls-file F`` counts release calls per booking in F, so the two-call limit holds across this
proof's teardown and a later ``--release-only`` run that passes the same file.
Run from meridian/ with the backend on 127.0.0.1:8013 and AWS_PROFILE set.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
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
from scripts.agentcore_caller import (  # noqa: E402
    bearer_headers, caller_scope, require_token_safe_url, user_for_traveler,
)
from scripts.kill_and_resume_proof import (  # noqa: E402
    _count,
    _holds_for,
    _purge,
    pinned_to_traveler,
    read_newest_snapshot,
)

API = os.getenv("MERIDIAN_PROOF_API", "http://127.0.0.1:8013/api")
AUTH_HEADERS: dict[str, str] = {}
TRAVELER = os.getenv("DEMO_TRAVELER_ID", "trv_meridian_demo")
PROOF_THREAD_PREFIX = "phase5-proof-"
THREAD_PATTERN = re.compile(r"^phase5-proof-[0-9a-f]{8,32}$")
RELEASE_CALLS_PER_HOLD = 2
RUN_HOLDS_SQL = """
SELECT hr.booking_id, hr.hold_request_id, hr.journey_id
  FROM journey_threads jt
  JOIN hold_requests hr ON hr.journey_id = jt.journey_id
 WHERE jt.thread_id = %s
 ORDER BY hr.booking_id
"""
THREADS_OF_JOURNEY_SQL = "SELECT COUNT(*) AS n FROM journey_threads WHERE journey_id = %s"
ACTIVE_SQL = ("SELECT (status = 'held' AND hold_expires_at > CURRENT_TIMESTAMP) AS active "
              "FROM bookings WHERE booking_id = %s")
CANONICAL = ("My JFK-to-Tokyo flight was canceled. Rework the trip, then check duration "
             "availability for the best three options.")
RESUME_MESSAGE = "Resume workflow from the saved step"
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
        headers={"Content-Type": "application/json", **AUTH_HEADERS},
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
        "conversation_id": thread, "travelers_count": 2, "intent": "recovery",
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
    """Delete a thread's rows when it has no journey, then raise if any survive."""
    tables = (("workflow_snapshots", "session_id"), ("journey_executions", "thread_id"))
    for table, column in tables:
        await client.execute(f"DELETE FROM {table} WHERE {column} = %s", (thread,))
    left = {table: await _count(client, table, column, thread) for table, column in tables}
    if any(left.values()):
        raise RuntimeError(f"the purge of thread {thread} left rows behind: "
                           + ", ".join(f"{t}={n}" for t, n in left.items() if n))


async def _check_owned(client, journey: str, thread: str) -> None:
    """Refuse to delete a journey that has any thread besides the proof's own."""
    rows = await client.execute(
        "SELECT COUNT(*) AS n FROM journey_threads WHERE journey_id = %s", (journey,))
    if int(rows[0]["n"]) != 1:
        raise RuntimeError(f"journey {journey} has other threads than {thread}; "
                           "not purging a journey this proof did not create")


async def _purge_run(client, thread: str) -> None:
    if not thread.startswith(PROOF_THREAD_PREFIX):
        raise RuntimeError(f"thread {thread} was not created by this proof; not purging it")
    rows = await client.execute("SELECT journey_id FROM journey_threads WHERE thread_id = %s",
                                (thread,))
    if not rows:
        await _purge_by_thread(client, thread)
        print(f"cleanup: no journey row for {thread}; purged its snapshots and executions")
        return
    journey = rows[0]["journey_id"]
    await _check_owned(client, journey, thread)
    await _purge(client, journey, thread, TRAVELER)
    print(f"cleanup: purged {journey} and counted every table of the run at zero")


def _cause(exc: BaseException) -> str:
    """A cause that is safe to print: the exception type, never its text."""
    return type(exc).__name__


async def _hold_state(client, booking: str) -> tuple[Optional[bool], str]:
    """Whether the hold still reserves places, read pinned to the traveler.

    ``bookings`` forces row level security, so the read runs in the traveler's pinned scope; a
    row that cannot be seen there is unknown (None), never taken for a released hold.
    """
    try:
        async with pinned_to_traveler(client, TRAVELER) as transaction:
            rows = await client.execute(ACTIVE_SQL, (booking,), transaction_id=transaction)
    except Exception as exc:  # noqa: BLE001 - an unknown state is reported, never assumed
        return None, f"read-back failed ({_cause(exc)})"
    if not rows or rows[0]["active"] is None:
        return None, "booking row not visible or its state unknown"
    return bool(rows[0]["active"]), ""


def _call_release(call_tool, args: dict) -> str:
    """One governed release call: '' when the Gateway answered released, else a fixed cause."""
    from backend.agents.phase_05_workflow.governed_hold import release_governed_hold

    try:
        outcome = release_governed_hold(call_tool, args)
    except Exception as exc:  # noqa: BLE001 - an ambiguous reply: the next read-back decides
        return f"release reply lost ({_cause(exc)})"
    return "" if outcome.released else f"release not confirmed ({outcome.error})"


class CallLedger:
    """Release calls already sent per booking: in memory, or in a file other processes share."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path, self.sent = path, {}

    def made(self, booking: str) -> int:
        if self.path is None:
            return self.sent.get(booking, 0)
        return self.path.read_text().split().count(booking) if self.path.exists() else 0

    def record(self, booking: str) -> None:
        """Count the call before it is sent, so a call that dies on the way still counts."""
        if self.path is None:
            self.sent[booking] = self.sent.get(booking, 0) + 1
            return
        with open(self.path, "a", encoding="utf-8") as ledger:
            ledger.write(booking + "\n")


async def _release_one(client, call_tool, args: dict, booking: str, ledger: CallLedger) -> str:
    """Release one hold with at most two calls in all; '' once it is inactive, else the causes.

    It is read back before each call, and a call after the first goes out only when that
    read-back succeeded and showed the hold still held, so an unconfirmed release is never
    repeated blind. Calls an earlier process recorded in the ledger count toward the two.
    """
    causes: list[str] = []
    sent_here = 0
    while True:
        made = max(ledger.made(booking), sent_here)
        active, read_cause = await _hold_state(client, booking)
        if active is False:
            return ""
        causes.append(read_cause)
        if made >= RELEASE_CALLS_PER_HOLD or (made and active is None):
            break
        ledger.record(booking)
        sent_here += 1
        causes.append(_call_release(call_tool, args))
    return "; ".join(dict.fromkeys(cause for cause in causes if cause)) or "still held"


async def _run_holds(client, thread: str) -> tuple[list[dict], list[str]]:
    """The holds this run's thread made, and why any of them must not be touched."""
    if not THREAD_PATTERN.match(thread):
        return [], [f"{thread} is not a thread this proof creates; nothing was released"]
    try:
        holds = [dict(row) for row in await client.execute(RUN_HOLDS_SQL, (thread,))]
        shared = []
        for journey in sorted({hold["journey_id"] for hold in holds}):
            rows = await client.execute(THREADS_OF_JOURNEY_SQL, (journey,))
            if int(rows[0]["n"]) != 1:
                shared.append(journey)
    except Exception as exc:  # noqa: BLE001 - reported; nothing is released blind
        return [], [f"reading the holds of {thread} failed ({_cause(exc)})"]
    problems = [f"journey {journey} has other threads; its holds were not released"
                for journey in shared]
    return [hold for hold in holds if hold["journey_id"] not in shared], problems


async def _release_holds(client, holds: list[dict], ledger: CallLedger) -> list[str]:
    """Release each hold through the Gateway as the traveler; every hold is attempted."""
    from backend.agentcore.gateway import get_agentcore_gateway
    from backend.agents.phase_05_workflow.governed_hold import release_arguments

    problems = []
    with caller_scope(user_for_traveler(TRAVELER)):
        call_tool = get_agentcore_gateway().call_tool
        for hold in holds:
            booking = hold["booking_id"]
            try:
                args = release_arguments(traveler_id=TRAVELER,
                                         hold_request_id=hold["hold_request_id"],
                                         journey_id=hold["journey_id"])
                cause = await _release_one(client, call_tool, args, booking, ledger)
            except Exception as exc:  # noqa: BLE001 - one bad hold never stops the others
                cause = f"cleanup error ({_cause(exc)})"
            if cause:
                problems.append(f"booking {booking} still held or unknown ({cause})")
            else:
                print(f"released: booking {booking} request {hold['hold_request_id']} "
                      f"journey {hold['journey_id']}")
    return problems


async def release_run(client, thread: str, ledger: Optional[CallLedger] = None) -> list[str]:
    """Give back every active hold of this run's thread; delete nothing.

    Only the holds whose journey belongs to ``thread`` alone are touched. Returns the problems,
    each with a sanitized cause; empty when every hold of the run is inactive.
    """
    holds, problems = await _run_holds(client, thread)
    if not holds:
        return problems
    try:
        return problems + await _release_holds(client, holds, ledger or CallLedger())
    except Exception as exc:  # noqa: BLE001 - no token or gateway: report every hold
        return problems + [f"booking {hold['booking_id']} not released: no governed release "
                           f"possible ({_cause(exc)})" for hold in holds]


async def _cleanup(client, thread: str, ledger: CallLedger) -> bool:
    """Release the run's holds and print any problem; never raises, so nothing is hidden."""
    problems = await release_run(client, thread, ledger)
    for problem in problems:
        print(f"cleanup FAILED for {thread}: {problem}", file=sys.stderr)
    return not problems


async def release_only(thread: str, ledger: CallLedger) -> int:
    """``--release-only``: give back the thread's active holds; 0 when none is left active."""
    problems = await release_run(get_rds_data_client(), thread, ledger)
    for problem in problems:
        print(f"cleanup FAILED for {thread}: {problem}", file=sys.stderr)
    print(f"RESULT: {'RELEASED' if not problems else 'NOT RELEASED'} ({thread})")
    return 0 if not problems else 1


async def _prove(client, thread: str, during: str) -> int:
    """Drive the proof and print its verdict; 0 pass, 1 fail, 2 missed the window."""
    try:
        journey, expected, case = await _drive(client, thread, during)
        document = await asyncio.to_thread(call, f"/journeys/{journey}")
        holds = await _holds_for(client, journey)
        failures = check_restart(document, holds, during, saved_hold_request_id=expected)
        _print_evidence(document, holds, expected, case)
    except MissedWindow as exc:
        print(f"RESULT: {exc}")
        return 2
    for line in failures:
        print(f"[BAD] {line}")
    print(f"RESULT: {'PASS' if not failures else 'FAIL'} ({during})")
    return 0 if not failures else 1


async def run(during: str, keep: bool, thread: Optional[str] = None,
              ledger: Optional[CallLedger] = None) -> int:
    """Run one proof in the given mode and return its exit code (0, 1 or 2).

    The verdict is printed before the clean-up runs; a clean-up problem is printed after it,
    turns a pass into exit 1 and never replaces an error already in flight.
    """
    client = get_rds_data_client()
    thread = thread or f"{PROOF_THREAD_PREFIX}{uuid.uuid4().hex[:12]}"
    print(f"thread: {thread}")
    code = 1
    try:
        code = await _prove(client, thread, during)
    finally:
        released = keep or await _cleanup(client, thread, ledger or CallLedger())
    if not released and code == 0:
        print(f"RESULT: FAIL ({during}): the run's hold was not released")
        return 1
    return code


def build_parser() -> argparse.ArgumentParser:
    """The command line."""
    parser = argparse.ArgumentParser(
        description="Prove a stopped Runtime session resumes.", allow_abbrev=False)
    parser.add_argument("--during", choices=sorted(EXPECTED_STATUSES), default="waiting",
                        help="stop the session while it waits for review or while it runs")
    parser.add_argument("--keep", action="store_true",
                        help="leave the run's hold held (nothing is deleted either way)")
    parser.add_argument("--thread", type=_proof_thread, default=None,
                        help="the run's thread, phase5-proof- and 8 to 32 hex digits")
    parser.add_argument("--release-only", action="store_true",
                        help="only give back the active holds of --thread")
    parser.add_argument("--calls-file", type=Path, default=None,
                        help="count release calls per booking in this file, across processes")
    return parser


def _proof_thread(value: str) -> str:
    if not THREAD_PATTERN.match(value):
        raise argparse.ArgumentTypeError(
            f"{value!r} is not phase5-proof- followed by 8 to 32 hex digits")
    return value


def main(argv: Optional[list[str]] = None) -> int:
    """Parse arguments and run the proof."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.release_only:
        if not args.thread:
            parser.error("--release-only needs --thread")
        return asyncio.run(release_only(args.thread, CallLedger(args.calls_file)))
    require_token_safe_url(API)
    AUTH_HEADERS.update(bearer_headers(user_for_traveler(TRAVELER)))
    try:
        return asyncio.run(run(args.during, args.keep, args.thread, CallLedger(args.calls_file)))
    finally:
        AUTH_HEADERS.clear()


if __name__ == "__main__":
    raise SystemExit(main())
