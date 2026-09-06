"""Kill a worker mid-workflow and resume it on another, against real Aurora.

The sequence the chalk talk demonstrates, run for real:

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
    python scripts/kill_and_resume_demo.py            # run the demo
    python scripts/kill_and_resume_demo.py --keep     # leave the rows behind
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from backend.agentcore.identity import get_agentcore_identity  # noqa: E402
from backend.agents.orchestration_05.workflow import (  # noqa: E402
    WORKER_INSTANCE_ID,
    initialize_checkpoint_backend,
)
from backend.db.journey_store import (  # noqa: E402
    ScopedDb,
    bind_thread,
    claim_execution,
    create_journey,
    renew_lease,
)
from backend.db.rds_data_client import get_rds_data_client  # noqa: E402

TRAVELER = os.getenv("DEMO_TRAVELER_ID", "trv_meridian_demo")
LEASE_SECONDS = int(os.getenv("DEMO_LEASE_SECONDS", "9"))
QUERY = (
    "My flight was cancelled, rework the trip and show duration availability."
)

BLUE, GREEN, RED, DIM, BOLD, OFF = (
    "\033[34m", "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m",
)


def say(step: str, message: str, colour: str = BLUE) -> None:
    print(f"{colour}{BOLD}{step:>10}{OFF}  {message}", flush=True)


def scoped(client):
    return client.scoped_session(
        traveler_id=TRAVELER,
        agent_type="booking_agent",
        authorization=get_agentcore_identity().authorization_context(),
    )


async def _run_workflow(thread_id: str, *, resume: bool) -> dict:
    """Run the real graph, with the app's own retrieval functions."""
    from backend.agents.orchestration_05.workflow import OrchestrationAgent
    from backend.routers.chat import (
        retrieval_availability_search,
        retrieval_search,
        workflow_memory_recall,
    )

    workflow = OrchestrationAgent(
        search_fn=retrieval_search,
        availability_fn=retrieval_availability_search,
        memory_recall_fn=workflow_memory_recall,
    )
    return await workflow.run(
        QUERY, traveler_id=TRAVELER, conversation_id=thread_id, resume=resume
    )


# --------------------------------------------------------------- worker one


async def _heartbeat(client, execution_id: str) -> None:
    """Keep the lease alive for as long as this worker is.

    Without this the lease is a timeout on the whole execution rather than a
    liveness signal: any run longer than the lease gets taken over while it is
    still working. It is also what makes the kill mean something, because a
    SIGKILLed worker stops renewing and loses the slot on its own.
    """
    interval = max(1, LEASE_SECONDS // 3)
    while True:
        await asyncio.sleep(interval)
        if not await renew_lease(client, execution_id, lease_seconds=LEASE_SECONDS):
            return  # The slot was taken; stop pretending to hold it.


async def _worker_one(journey_id: str, thread_id: str) -> None:
    """Claim the slot, run to the interrupt, then idle until killed."""
    os.environ["LANGGRAPH_DEMO_INTERRUPT_AFTER"] = "search"
    client = get_rds_data_client()

    claim = await claim_execution(
        client, journey_id, thread_id, WORKER_INSTANCE_ID, lease_seconds=LEASE_SECONDS
    )
    heartbeat = asyncio.create_task(_heartbeat(client, claim.execution_id))
    print(
        json.dumps(
            {
                "event": "claimed",
                "worker_id": WORKER_INSTANCE_ID,
                "execution_id": claim.execution_id,
                "attempt": claim.attempt,
            }
        ),
        flush=True,
    )

    state = await _run_workflow(thread_id, resume=False)
    print(
        json.dumps({"event": "paused", "status": state.get("workflow_status")}),
        flush=True,
    )

    await heartbeat  # Hold the lease, by renewing it, until SIGKILL.


# ------------------------------------------------------------------- driver


async def _read_committed_checkpoint(client, thread_id: str) -> dict | None:
    rows = await client.execute(
        """
        SELECT checkpoint_id, parent_checkpoint_id
          FROM checkpoints WHERE thread_id = %s
         ORDER BY checkpoint_id DESC LIMIT 1
        """,
        (thread_id,),
    )
    return rows[0] if rows else None


async def _holds_for(client, journey_id: str) -> list[dict]:
    return await client.execute(
        """
        SELECT hr.hold_request_id, hr.booking_id, b.status
          FROM hold_requests hr
          JOIN bookings b ON b.booking_id = hr.booking_id
         WHERE hr.journey_id = %s
        """,
        (journey_id,),
    )


async def _purge(client, journey_id: str, thread_id: str) -> None:
    for row in await _holds_for(client, journey_id):
        for table in ("hold_requests", "booking_lines", "bookings"):
            await client.execute(
                f"DELETE FROM {table} WHERE booking_id = %s", (row["booking_id"],)
            )
    for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
        await client.execute(
            f"DELETE FROM {table} WHERE thread_id = %s", (thread_id,)
        )
    await client.execute(
        "DELETE FROM journey_executions WHERE thread_id = %s", (thread_id,)
    )
    await client.execute(
        "UPDATE journeys SET active_thread_id = NULL WHERE journey_id = %s",
        (journey_id,),
    )
    await client.execute(
        "DELETE FROM journey_threads WHERE thread_id = %s", (thread_id,)
    )
    await client.execute(
        "DELETE FROM hold_requests WHERE journey_id = %s", (journey_id,)
    )
    await client.execute("DELETE FROM journeys WHERE journey_id = %s", (journey_id,))


async def main(keep: bool) -> int:
    client = get_rds_data_client()

    backend = await initialize_checkpoint_backend()
    say("backend", f"{backend.kind} · durable={backend.durable}")
    if not backend.durable:
        say("abort", "checkpoints are not durable; nothing to demonstrate", RED)
        return 1

    async with scoped(client) as tx:
        db = ScopedDb(client, tx)
        journey_id = await create_journey(db, TRAVELER, backend.kind)
        thread_id = f"demo-{uuid.uuid4().hex[:10]}"
        await bind_thread(db, journey_id, thread_id)
    say("journey", f"{journey_id} · thread {thread_id}")

    child = subprocess.Popen(
        [sys.executable, __file__, "--worker-one", journey_id, thread_id],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    first_worker = ""
    try:
        for line in child.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            event = json.loads(line)
            if event["event"] == "claimed":
                first_worker = event["worker_id"]
                say(
                    "worker 1",
                    f"claimed attempt {event['attempt']} as {first_worker} "
                    f"(pid {child.pid}, lease {LEASE_SECONDS}s)",
                )
            elif event["event"] == "paused":
                say("worker 1", f"workflow {event['status']} at a checkpoint")
                break

        committed = await _read_committed_checkpoint(client, thread_id)
        if not committed:
            say("abort", "no checkpoint reached Aurora", RED)
            return 1
        say("aurora", f"committed checkpoint {committed['checkpoint_id']}", GREEN)

        say("kill", f"SIGKILL {child.pid}", RED)
        os.kill(child.pid, signal.SIGKILL)
        child.wait(timeout=10)
        say("kill", f"worker 1 is gone (exit {child.returncode})", RED)

        refused = await claim_execution(
            client, journey_id, thread_id, WORKER_INSTANCE_ID
        )
        if refused.claimed:
            say(
                "worker 2",
                "claimed immediately - worker 1's lease was already stale",
                RED,
            )
        else:
            say(
                "worker 2",
                f"refused: {refused.conflict['worker_id']} still holds the lease",
            )

        say("wait", f"{LEASE_SECONDS + 2}s for the dead worker's lease to expire", DIM)
        time.sleep(LEASE_SECONDS + 2)

        taken = await claim_execution(client, journey_id, thread_id, WORKER_INSTANCE_ID)
        if not taken.claimed:
            say("abort", "takeover refused after the lease expired", RED)
            return 1
        say(
            "worker 2",
            f"claimed attempt {taken.attempt} as {WORKER_INSTANCE_ID} "
            f"(was {first_worker})",
            GREEN,
        )

        os.environ.pop("LANGGRAPH_DEMO_INTERRUPT_AFTER", None)
        state = await _run_workflow(thread_id, resume=True)
        say("resume", f"workflow {state.get('workflow_status')} on the same thread", GREEN)
        say(
            "resume",
            f"worker restart {'observed' if state.get('resumed_after_restart') else 'not observed'}",
        )

        holds = await _holds_for(client, journey_id)
        say("aurora", f"holds recorded for this journey: {len(holds)}", GREEN)
        for row in holds:
            say("hold", f"{row['hold_request_id']} -> {row['booking_id']} ({row['status']})")
        if len(holds) > 1:
            say("abort", "a restart produced more than one hold", RED)
            return 1

        history = await client.execute(
            "SELECT count(*) AS n FROM checkpoints WHERE thread_id = %s", (thread_id,)
        )
        say("aurora", f"checkpoints on this thread: {history[0]['n']}")
        return 0
    finally:
        if child.poll() is None:
            child.kill()
        if keep:
            say("keep", f"left journey {journey_id} and thread {thread_id} in place", DIM)
        else:
            await _purge(client, journey_id, thread_id)
            say("cleanup", "demo rows removed", DIM)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="leave the rows behind")
    parser.add_argument("--worker-one", nargs=2, metavar=("JOURNEY", "THREAD"))
    args = parser.parse_args()

    if args.worker_one:
        asyncio.run(_worker_one(*args.worker_one))
    else:
        raise SystemExit(asyncio.run(main(args.keep)))
