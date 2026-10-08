"""Lose a real Gateway hold reply, then resume the same intent on a new worker.

The fault is injected only in this CLI: after Gateway returns a committed hold,
the wrapper discards that response and raises TimeoutError. Aurora, Gateway,
Cedar, retrieval, and checkpointing are real. This is a lost-reply simulation,
not a claim that the network itself failed.

Usage: python scripts/lost_response_proof.py [--worker-login]

With --worker-login the injected-loss worker and the replacement worker each run as a
subprocess as the meridian_workflow login (their AURORA_SECRET_ARN becomes
AURORA_WORKFLOW_SECRET_ARN). The driver keeps the master client for verification and
cleanup, and fails unless every worker reports ``current_user`` as meridian_workflow.

Creates isolated rehearsal rows and removes them in finally. No existing
bookings, schema, permissions, or service configuration are changed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.kill_and_resume_proof import (  # noqa: E402
    TRAVELER, ScopedDb, _holds_for, _purge, _run_workflow, bind_thread,
    create_journey, get_rds_data_client, read_newest_snapshot, report_worker_identity,
    require_worker_login, run_takeover, say, scoped, worker_env,
)
from backend.agentcore.gateway import AgentCoreGatewayAdapter  # noqa: E402
from scripts.agentcore_caller import caller_scope, user_for_traveler  # noqa: E402
from backend.agents.phase_05_workflow.governed_hold import (  # noqa: E402
    HOLD_TOOL, HoldOutcomeUnknown, hold_arguments, place_governed_hold,
)
from backend.agents.phase_05_workflow.graph import fold_snapshot  # noqa: E402
from backend.agents.phase_05_workflow.service import workflow_store_status  # noqa: E402

EXPECTED_LOSS_EXIT = 75


def drop_committed_hold_reply(call_tool):
    """Keep the real side effect; discard its acknowledgement at the worker."""
    def wrapped(name, arguments):
        response = call_tool(name, arguments)
        if name == HOLD_TOOL:
            observed = place_governed_hold(lambda *_: response, arguments)
            if observed.placed and observed.hold.get("status") == "held":
                print(json.dumps({
                    "event": "reply_lost",
                    "booking_id": observed.hold["bookingId"],
                }), flush=True)
                raise TimeoutError("Injected response loss after the Gateway committed the hold")
        return response
    return wrapped


def check_worker_identity(events: list[dict], worker_login: bool) -> None:
    """With --worker-login, the worker must have reported the workflow login."""
    if worker_login:
        identity = next((e for e in events if e["event"] == "identity"), {})
        require_worker_login(identity.get("current_user"))


async def worker(thread_id: str) -> int:
    await report_worker_identity()
    paused = await _run_workflow(thread_id, resume=False)
    if paused.get("workflow_status") != "paused":
        return 1  # A fresh run must stop for the traveler's review before any hold.
    try:
        # The traveler's resume is what confirms the hold.
        await _run_workflow(
            thread_id, resume=True, gateway_wrapper=drop_committed_hold_reply,
        )
    except HoldOutcomeUnknown:
        return EXPECTED_LOSS_EXIT
    return 1  # No injected loss, or the workflow incorrectly swallowed it.


def check_denials(intent: dict, thread_id: str) -> None:
    """Use the existing request identity so a failed denial cannot add a hold."""
    gateway = AgentCoreGatewayAdapter()
    arguments = hold_arguments(
        intent, traveler_id=TRAVELER, journey_ref=thread_id,
        budget_ceiling_cents=int(round(float(intent["total_amount"]) * 100)),
        hold_minutes=15, execution_id=None, traveler_confirmed=True,
    )
    for label, changes in (
        ("unconfirmed", {"travelerConfirmed": False}),
        ("over budget", {"budgetCeilingCents": 1}),
    ):
        result = place_governed_hold(gateway.call_tool, {**arguments, **changes})
        if result.policy_decision != "deny" or result.placed:
            raise AssertionError(f"{label}: expected a Cedar denial, got {result}")
        say("Cedar", f"{label}: denied by Gateway policy")


async def main(worker_login: bool = False) -> int:
    env = worker_env(worker_login)
    client = get_rds_data_client()
    store = workflow_store_status()
    async with scoped(client) as tx:
        db = ScopedDb(client, tx)
        journey_id = await create_journey(db, TRAVELER, store["kind"])
        thread_id = f"loss-{uuid.uuid4().hex[:10]}"
        await bind_thread(db, journey_id, thread_id)
    say("journey", f"{journey_id} · thread {thread_id}")
    child = None
    try:
        child = await asyncio.create_subprocess_exec(
            sys.executable, __file__, "--worker", thread_id,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
        )
        stdout, stderr = await asyncio.wait_for(child.communicate(), timeout=240)
        events = [
            json.loads(line) for line in stdout.decode().splitlines()
            if line.startswith('{"event":')
        ]
        lost = next((event for event in events if event["event"] == "reply_lost"), None)
        if child.returncode != EXPECTED_LOSS_EXIT or not lost:
            raise AssertionError(
                f"Worker did not stop at the injected loss: exit={child.returncode}; "
                f"{stderr.decode()[-1500:]}"
            )
        check_worker_identity(events, worker_login)

        before = await _holds_for(client, journey_id)
        if len(before) != 1 or before[0]["booking_id"] != lost["booking_id"]:
            raise AssertionError("Expected exactly one committed hold before retry")
        snapshot = await read_newest_snapshot(client, thread_id)
        if snapshot is None:
            raise AssertionError("No workflow snapshot reached Aurora")
        values = fold_snapshot(snapshot)
        intent = values["hold_intent"]
        if values.get("hold_id") or intent["booking_id"] != before[0]["booking_id"]:
            raise AssertionError("Expected the prepared intent without a hold acknowledgement")
        say("loss", "Aurora has the hold; the worker checkpoint has only the prepared intent")
        await asyncio.to_thread(check_denials, intent, thread_id)

        result = await run_takeover(thread_id, worker_login)
        after = await _holds_for(client, journey_id)
        identity = lambda row: (row["hold_request_id"], row["booking_id"], row["hold_expires_at"])
        if len(after) != 1 or identity(before[0]) != identity(after[0]):
            raise AssertionError("Retry changed the request, booking, count, or original expiry")
        if result.get("workflow_status") != "resumed":
            raise AssertionError("Replacement workflow did not finish its resume")
        if result.get("hold_id") != before[0]["booking_id"]:
            raise AssertionError("Replacement worker did not receive the persisted hold")
        if result.get("resumed_from_checkpoint") != snapshot.get("created_at"):
            raise AssertionError("Replacement worker resumed a different checkpoint")
        executions = await client.execute(
            "SELECT status, worker_id FROM journey_executions "
            "WHERE thread_id = %s ORDER BY attempt", (thread_id,),
        )
        if [row["status"] for row in executions] != ["paused", "failed", "succeeded"]:
            raise AssertionError(f"Unexpected execution outcomes: {executions}")
        if executions[1]["worker_id"] == executions[2]["worker_id"]:
            raise AssertionError("Resume must use a different worker")
        say("resume", f"same request and booking {after[0]['booking_id']}; one persisted hold")
        say("expiry", f"original expiry retained: {after[0]['hold_expires_at']}")
        say("workers", "review paused; confirmed run failed; replacement resumed and succeeded")
        return 0
    finally:
        if child is not None and child.returncode is None:
            child.kill()
            await child.wait()
        await _purge(client, journey_id, thread_id)
        say("cleanup", "isolated rehearsal rows removed")


def build_parser() -> argparse.ArgumentParser:
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--worker-login", action="store_true",
        help="run the worker subprocess as the meridian_workflow login",
    )
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    with caller_scope(user_for_traveler(TRAVELER)):
        raise SystemExit(
            asyncio.run(worker(args.worker) if args.worker else main(args.worker_login)))
