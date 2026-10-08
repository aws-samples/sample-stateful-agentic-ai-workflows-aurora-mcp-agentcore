"""Kill a worker mid-workflow and resume it on another, against real Aurora.

The durable-recovery sequence, run for real:

  1. Worker one claims the thread's single execution slot and runs the
     workflow to its interrupt point, committing a checkpoint.
  2. The committed checkpoint id is read back out of Aurora.
  3. Worker one is SIGKILLed. Nothing is given a chance to clean up.
  4. Worker two tries to claim the slot and is refused, because the dead
     worker's lease has not expired yet.
  5. The lease expires. Worker two claims the slot on a new attempt.
  6. Worker two resumes the thread and finishes it, and the hold placed
     across the restart exists exactly once.

Every value printed is read back from Aurora rather than held in memory, so
what the room sees is what actually persisted.

Usage:
    python scripts/kill_and_resume_proof.py            # run the proof
    python scripts/kill_and_resume_proof.py --keep     # leave the rows behind
    python scripts/kill_and_resume_proof.py --worker-login
        # run the SIGKILLed worker as the meridian_workflow login

With --worker-login the SIGKILLed worker and the takeover worker each run as a
subprocess with AURORA_WORKFLOW_SECRET_ARN as its AURORA_SECRET_ARN. The driver keeps
the master client for verification and cleanup, and fails unless every worker reports
``current_user`` as meridian_workflow.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import uuid
from collections.abc import Mapping
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from backend.agentcore.identity import get_agentcore_identity  # noqa: E402
from backend.agents.phase_05_workflow import service  # noqa: E402
from backend.agents.phase_05_workflow.nodes import WorkflowNodes  # noqa: E402
from backend.agents.phase_05_workflow.runner import (  # noqa: E402
    WorkflowCommand,
    WorkflowConflictError,
)
from backend.db.journey_store import (  # noqa: E402
    ScopedDb,
    bind_thread,
    create_journey,
)
from backend.db.rds_data_client import get_rds_data_client  # noqa: E402
from scripts.agentcore_caller import caller_scope, user_for_traveler  # noqa: E402

TRAVELER = os.getenv("DEMO_TRAVELER_ID", "trv_meridian_demo")
# A cold worker spends several seconds in the search and availability nodes
# before its first heartbeat, so the lease must outlast that or the proof
# fails before the hold is placed. Twenty seconds keeps the takeover wait short.
LEASE_SECONDS = int(os.getenv("DEMO_LEASE_SECONDS", "20"))
QUERY = (
    "My flight was cancelled, rework the trip and show duration availability."
)

BOOKING_AGENT = "booking_agent"
PINNED_TABLES = ("booking_lines", "bookings")
UNPINNED_TABLES = (
    ("hold_requests", "journey_id"), ("journeys", "journey_id"),
    ("journey_executions", "thread_id"), ("journey_threads", "thread_id"),
    ("workflow_snapshots", "session_id"), ("workflow_session_stops", "journey_id"),
)
WORKFLOW_LOGIN = "meridian_workflow"
CONFLICT_EXIT = 76
STDERR_TAIL_LINES = 20

BLUE, GREEN, RED, DIM, BOLD, OFF = (
    "\033[34m", "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m",
)


def say(step: str, message: str, colour: str = BLUE) -> None:
    print(f"{colour}{BOLD}{step:>10}{OFF}  {message}", flush=True)


def worker_env(worker_login: bool, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment for a worker subprocess; only it ever runs as the login."""
    env = dict(os.environ if environ is None else environ)
    if worker_login:
        secret = env.get("AURORA_WORKFLOW_SECRET_ARN")
        if not secret:
            raise SystemExit(
                "--worker-login needs AURORA_WORKFLOW_SECRET_ARN; "
                "run scripts/provision_workflow_login.py and load .env"
            )
        env["AURORA_SECRET_ARN"] = secret
    return env


def require_worker_login(current_user: str | None) -> None:
    """Fail unless the worker really ran as the workflow login, not the master role."""
    if current_user != WORKFLOW_LOGIN:
        raise AssertionError(
            f"The worker must run as {WORKFLOW_LOGIN}, but it reported {current_user!r}"
        )


async def report_worker_identity() -> None:
    """Print the database user this worker's Aurora client really runs as."""
    rows = await get_rds_data_client().execute("SELECT current_user AS db_user")
    print(json.dumps({"event": "identity", "current_user": rows[0]["db_user"]}), flush=True)


def scoped(client):
    return client.scoped_session(
        traveler_id=TRAVELER,
        agent_type="booking_agent",
        authorization=get_agentcore_identity().authorization_context(),
    )


async def _run_workflow(
    thread_id: str,
    *,
    resume: bool,
    pause_after: str | None = None,
    after_pause=None,
    gateway_wrapper=None,
) -> dict:
    """Run the real graph, with the app's own retrieval functions."""
    gateway_call = (
        gateway_wrapper(WorkflowNodes._configured_gateway) if gateway_wrapper else None
    )
    runner = service.build_workflow_runner(
        lease_seconds=LEASE_SECONDS,
        heartbeat_seconds=max(1, LEASE_SECONDS // 3),
        gateway_call=gateway_call,
        pause_after=pause_after,
    )
    command = WorkflowCommand(
        query=QUERY,
        traveler_id=TRAVELER,
        thread_id=thread_id,
        resume=resume,
        travelers_count=2,
    )
    return await runner.run(command, after_pause=after_pause)


async def _worker_one(journey_id: str, thread_id: str) -> None:
    """Pause for review, resume to commit the hold, then stay alive until killed."""
    await report_worker_identity()
    reviewed = await _run_workflow(thread_id, resume=False)
    if reviewed.get("workflow_status") != "paused":
        raise RuntimeError("A fresh run must stop for the traveler's review before any hold")

    async def wait_for_kill(state, claim):
        print(json.dumps({"event": "claimed", "worker_id": claim.worker_id,
                          "execution_id": claim.execution_id, "attempt": claim.attempt}), flush=True)
        print(json.dumps({"event": "paused", "status": state["workflow_status"]}), flush=True)
        await asyncio.Event().wait()

    await _run_workflow(
        thread_id, resume=True, pause_after="hold", after_pause=wait_for_kill
    )


async def _worker_two(thread_id: str) -> int:
    """The takeover: resume the thread, report who ran it and what it returned."""
    await report_worker_identity()
    try:
        state = await _run_workflow(thread_id, resume=True)
    except WorkflowConflictError as exc:
        print(json.dumps({"event": "conflict", "message": str(exc)}), flush=True)
        return CONFLICT_EXIT
    print(json.dumps({"event": "result", "state": state}, default=str), flush=True)
    return 0


def _stderr_tail(stderr: str) -> str:
    return "\n".join(stderr.splitlines()[-STDERR_TAIL_LINES:])


def parse_takeover(returncode: int, stdout: str, stderr: str, worker_login: bool) -> dict:
    """Turn a takeover subprocess's output into its state, checking who ran it.

    The exit code is checked first so a worker that crashed before printing its
    identity (for example, missing grants) reports its stderr, not an identity
    assertion.
    """
    if returncode not in (0, CONFLICT_EXIT):
        raise RuntimeError(
            f"Takeover worker failed (exit {returncode}); stderr tail:\n{_stderr_tail(stderr)}"
        )
    events = [json.loads(line) for line in stdout.splitlines() if line.startswith('{"event":')]
    if worker_login:
        identity = next((e for e in events if e["event"] == "identity"), {})
        require_worker_login(identity.get("current_user"))
    if returncode == CONFLICT_EXIT:
        raise WorkflowConflictError(next(e["message"] for e in events if e["event"] == "conflict"))
    result = next((e for e in events if e["event"] == "result"), None)
    if result is None:
        raise RuntimeError(
            f"Takeover worker exited 0 but printed no result; stderr tail:\n{_stderr_tail(stderr)}"
        )
    return result["state"]


async def run_takeover(thread_id: str, worker_login: bool) -> dict:
    """Resume the thread; with --worker-login, in a subprocess that runs as the login."""
    if not worker_login:
        return await _run_workflow(thread_id, resume=True)
    child = await asyncio.create_subprocess_exec(
        sys.executable, __file__, "--worker-two", thread_id,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**worker_env(True), "PYTHONUNBUFFERED": "1"},
    )
    try:
        stdout, stderr = await asyncio.wait_for(child.communicate(), timeout=240)
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
    return parse_takeover(child.returncode, stdout.decode(), stderr.decode(), True)


# ------------------------------------------------------------------- driver


async def _read_committed_checkpoint(client, thread_id: str) -> dict | None:
    rows = await client.execute(
        """
        SELECT snapshot_seq::TEXT AS checkpoint_id, status
          FROM workflow_snapshots WHERE session_id = %s
         ORDER BY snapshot_seq DESC LIMIT 1
        """,
        (thread_id,),
    )
    return rows[0] if rows else None


async def read_newest_snapshot(client, thread_id: str) -> dict | None:
    """The newest saved snapshot envelope for a thread, as Aurora holds it."""
    rows = await client.execute(
        """
        SELECT snapshot::TEXT AS snapshot
          FROM workflow_snapshots WHERE session_id = %s
         ORDER BY snapshot_seq DESC LIMIT 1
        """,
        (thread_id,),
    )
    return json.loads(rows[0]["snapshot"]) if rows else None


async def _holds_for(client, journey_id: str) -> list[dict]:
    return await client.execute(
        """
        SELECT hr.hold_request_id, hr.booking_id, b.status, b.hold_expires_at::TEXT AS hold_expires_at
          FROM hold_requests hr
          JOIN bookings b ON b.booking_id = hr.booking_id
         WHERE hr.journey_id = %s
        """,
        (journey_id,),
    )


@asynccontextmanager
async def pinned_to_traveler(client, traveler_id: str):
    """A Data API transaction pinned to one traveler and the booking agent.

    ``bookings`` and ``booking_lines`` force row level security on these two settings, so a
    statement outside such a transaction can match zero rows without any error. Commits when
    the block ends, rolls back when it raises.
    """
    transaction = client.begin_transaction()
    try:
        await client.execute(
            "SELECT set_config('app.current_traveler_id', %s, true), "
            "set_config('app.agent_type', %s, true)",
            (traveler_id, BOOKING_AGENT), transaction_id=transaction)
        yield transaction
    except BaseException:
        client.rollback_transaction(transaction)
        raise
    client.commit_transaction(transaction)


async def _booking_ids(client, journey_id: str) -> list[str]:
    rows = await client.execute(
        "SELECT booking_id FROM hold_requests WHERE journey_id = %s", (journey_id,))
    return [row["booking_id"] for row in rows if row.get("booking_id")]


async def _count(client, table: str, column: str, value: str, transaction=None) -> int:
    rows = await client.execute(
        f"SELECT COUNT(*) AS n FROM {table} WHERE {column} = %s", (value,),
        transaction_id=transaction)
    return int(rows[0]["n"])


async def _invisible_bookings(client, bookings: list[str], traveler_id: str) -> int:
    """How many of the run's bookings the pinned scope cannot see; their deletion is unprovable."""
    async with pinned_to_traveler(client, traveler_id) as transaction:
        seen = sum([await _count(client, "bookings", "booking_id", booking, transaction)
                    for booking in bookings])
    return len(bookings) - seen


async def _remaining(client, keys: Mapping[str, str], bookings: list[str],
                     traveler_id: str) -> dict[str, int]:
    """Rows of the run still in Aurora, per table; the booking tables are read pinned."""
    left = {}
    for table, column in UNPINNED_TABLES:
        left[table] = await _count(client, table, column, keys[column])
    async with pinned_to_traveler(client, traveler_id) as transaction:
        for table in PINNED_TABLES:
            left[table] = sum([await _count(client, table, "booking_id", booking, transaction)
                               for booking in bookings])
    return left


async def _purge(client, journey_id: str, thread_id: str, traveler_id: str = TRAVELER) -> None:
    """Delete one run's rows, then count them again and raise if any table still has some.

    The booking ids are read before ``hold_requests`` goes, because that table is the only link
    to them. The booking tables are deleted and counted in a transaction pinned to the traveler.

    Raises:
        RuntimeError: When any table still holds a row of the run after the deletes.
    """
    bookings = await _booking_ids(client, journey_id)
    invisible = await _invisible_bookings(client, bookings, traveler_id)
    for booking in bookings:
        await client.execute("DELETE FROM hold_requests WHERE booking_id = %s", (booking,))
    async with pinned_to_traveler(client, traveler_id) as transaction:
        for booking in bookings:
            for table in PINNED_TABLES:
                await client.execute(f"DELETE FROM {table} WHERE booking_id = %s", (booking,),
                                     transaction_id=transaction)
    await client.execute("DELETE FROM workflow_session_stops WHERE journey_id = %s", (journey_id,))
    await client.execute("DELETE FROM workflow_snapshots WHERE session_id = %s", (thread_id,))
    await client.execute("DELETE FROM journey_executions WHERE thread_id = %s", (thread_id,))
    await client.execute(
        "UPDATE journeys SET active_thread_id = NULL WHERE journey_id = %s", (journey_id,))
    await client.execute("DELETE FROM journey_threads WHERE thread_id = %s", (thread_id,))
    await client.execute("DELETE FROM hold_requests WHERE journey_id = %s", (journey_id,))
    await client.execute("DELETE FROM journeys WHERE journey_id = %s", (journey_id,))
    left = await _remaining(
        client, {"journey_id": journey_id, "thread_id": thread_id, "session_id": thread_id},
        bookings, traveler_id)
    survivors = {table: count for table, count in left.items() if count}
    if invisible:
        survivors["bookings not visible as the traveler"] = invisible
    if survivors:
        raise RuntimeError(f"the purge of journey {journey_id} left rows behind: "
                           + ", ".join(f"{table}={count}" for table, count in survivors.items()))


async def _wait_for_pause(child, expect_login: bool = False) -> str:
    """Read startup events without blocking the event loop or waiting forever."""
    first_worker = ""
    reported_user = None
    while line := await child.stdout.readline():
        line = line.decode().strip()
        if not line.startswith("{"):
            continue
        event = json.loads(line)
        if event.get("event") == "identity":
            reported_user = event["current_user"]
            say("worker 1", f"database user {reported_user}")
        elif event.get("event") == "claimed":
            first_worker = event["worker_id"]
            say("worker 1", f"claimed attempt {event['attempt']} as {first_worker} "
                f"(pid {child.pid}, lease {LEASE_SECONDS}s)")
        elif event.get("event") == "paused":
            if not first_worker or event.get("status") != "paused":
                raise RuntimeError("Worker did not report a claimed, paused execution")
            if expect_login:
                require_worker_login(reported_user)
            say("worker 1", "workflow paused at a checkpoint")
            return first_worker
    raise RuntimeError("Worker exited before reaching its pause; inspect the worker output")


def _verify_replacement(state: dict, executions: list[dict], first_worker: str) -> None:
    if state.get("workflow_status") != "resumed" or not state.get("resumed_after_restart"):
        raise AssertionError("Replacement must finish a resume after worker restart")
    if (len(executions) < 2 or executions[0]["worker_id"] != first_worker
            or executions[-1]["worker_id"] == first_worker
            or executions[-1]["status"] != "succeeded"):
        raise AssertionError("Aurora must record a successful, different replacement worker")


async def main(keep: bool, worker_login: bool = False) -> int:
    env = {**worker_env(worker_login), "PYTHONUNBUFFERED": "1"}
    client = get_rds_data_client()

    store = service.workflow_store_status()
    say("backend", f"{store['kind']} · durable={store['durable']}")

    async with scoped(client) as tx:
        db = ScopedDb(client, tx)
        journey_id = await create_journey(db, TRAVELER, store["kind"])
        thread_id = f"proof-{uuid.uuid4().hex[:10]}"
        await bind_thread(db, journey_id, thread_id)
    say("journey", f"{journey_id} · thread {thread_id}")

    child = await asyncio.create_subprocess_exec(
        sys.executable, __file__, "--worker-one", journey_id, thread_id,
        stdout=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        first_worker = await asyncio.wait_for(
            _wait_for_pause(child, worker_login), timeout=240
        )

        committed = await _read_committed_checkpoint(client, thread_id)
        if not committed:
            say("abort", "no checkpoint reached Aurora", RED)
            return 1
        say("aurora", f"committed checkpoint {committed['checkpoint_id']}", GREEN)

        original_holds = await _holds_for(client, journey_id)
        if len(original_holds) != 1:
            say("abort", "the worker must commit one hold before interruption", RED)
            return 1
        say("hold", f"original expiry {original_holds[0]['hold_expires_at']}")
        say("kill", f"SIGKILL {child.pid}", RED)
        os.kill(child.pid, signal.SIGKILL)
        await asyncio.wait_for(child.wait(), timeout=10)
        say("kill", f"worker 1 is gone (exit {child.returncode})", RED)

        for attempt in range(60):
            try:
                state = await run_takeover(thread_id, worker_login)
                break
            except WorkflowConflictError:
                if attempt == 0:
                    say("worker 2", "takeover refused until the lease and any interrupted transaction clear")
                await asyncio.sleep(3)
        else:
            say("abort", "takeover remained unavailable; inspect the journey execution", RED)
            return 1
        executions = await client.execute(
            "SELECT worker_id, status FROM journey_executions WHERE thread_id = %s ORDER BY attempt",
            (thread_id,),
        )
        _verify_replacement(state, executions, first_worker)
        say("resume", f"workflow {state.get('workflow_status')} on the same thread", GREEN)
        say(
            "resume",
            f"worker restart {'observed' if state.get('resumed_after_restart') else 'not observed'}",
        )

        holds = await _holds_for(client, journey_id)
        say("aurora", f"holds recorded for this journey: {len(holds)}", GREEN)
        for row in holds:
            say("hold", f"{row['hold_request_id']} -> {row['booking_id']} ({row['status']})")
        if len(holds) != 1:
            say("abort", "expected exactly one hold after restart", RED)
            return 1
        if (holds[0]["booking_id"], holds[0]["hold_expires_at"]) != (
            original_holds[0]["booking_id"], original_holds[0]["hold_expires_at"]
        ):
            say("abort", "the booking identity or original expiry changed", RED)
            return 1
        say("hold", "same booking and original expiry after restart", GREEN)

        history = await client.execute(
            "SELECT count(*) AS n FROM workflow_snapshots WHERE session_id = %s", (thread_id,)
        )
        say("aurora", f"workflow snapshots on this thread: {history[0]['n']}")
        return 0
    finally:
        if child.returncode is None:
            child.kill()
            await child.wait()
        if keep:
            say("keep", f"left journey {journey_id} and thread {thread_id} in place", DIM)
        else:
            await _purge(client, journey_id, thread_id)
            say("cleanup", "rehearsal rows removed", DIM)


def build_parser() -> argparse.ArgumentParser:
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--keep", action="store_true", help="leave the rows behind")
    parser.add_argument(
        "--worker-login", action="store_true",
        help="run the SIGKILLed worker as the meridian_workflow login",
    )
    parser.add_argument("--worker-one", nargs=2, metavar=("JOURNEY", "THREAD"))
    parser.add_argument("--worker-two", metavar="THREAD", help=argparse.SUPPRESS)
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()

    with caller_scope(user_for_traveler(TRAVELER)):
        if args.worker_two:
            raise SystemExit(asyncio.run(_worker_two(args.worker_two)))
        if args.worker_one:
            asyncio.run(_worker_one(*args.worker_one))
        else:
            raise SystemExit(asyncio.run(main(args.keep, args.worker_login)))
