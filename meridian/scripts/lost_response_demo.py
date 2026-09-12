"""Lose a real Gateway hold reply, then resume the same intent on a new worker.

The fault is injected only in this CLI: after Gateway returns a committed hold,
the wrapper discards that response and raises TimeoutError. Aurora, Gateway,
Cedar, retrieval, and checkpointing are real. This is a lost-reply simulation,
not a claim that the network itself failed.

Usage: python scripts/lost_response_demo.py

Creates isolated rehearsal rows and removes them in finally. No existing
bookings, schema, permissions, or service configuration are changed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.kill_and_resume_demo import (  # noqa: E402
    TRAVELER, ScopedDb, _holds_for, _purge, _run_workflow, bind_thread,
    create_journey, get_rds_data_client, initialize_checkpoint_backend, say, scoped,
)
from backend.agentcore.gateway import AgentCoreGatewayAdapter  # noqa: E402
from backend.agents.orchestration_05.governed_hold import (  # noqa: E402
    HOLD_TOOL, HoldOutcomeUnknown, hold_arguments, place_governed_hold,
)

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


async def worker(thread_id: str) -> int:
    os.environ.pop("LANGGRAPH_DEMO_INTERRUPT_AFTER", None)
    try:
        await _run_workflow(
            thread_id, resume=False, gateway_call_wrapper=drop_committed_hold_reply,
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
        hold_minutes=15, execution_id=None,
    )
    for label, changes in (
        ("unconfirmed", {"travelerConfirmed": False}),
        ("over budget", {"budgetCeilingCents": 1}),
    ):
        result = place_governed_hold(gateway.call_tool, {**arguments, **changes})
        if result.policy_decision != "deny" or result.placed:
            raise AssertionError(f"{label}: expected a Cedar denial, got {result}")
        say("Cedar", f"{label}: denied by Gateway policy")


async def main() -> int:
    os.environ.pop("LANGGRAPH_DEMO_INTERRUPT_AFTER", None)
    client = get_rds_data_client()
    backend = await initialize_checkpoint_backend()
    if not backend.durable:
        raise RuntimeError("A durable Aurora saver is required")
    async with scoped(client) as tx:
        db = ScopedDb(client, tx)
        journey_id = await create_journey(db, TRAVELER, backend.kind)
        thread_id = f"loss-{uuid.uuid4().hex[:10]}"
        await bind_thread(db, journey_id, thread_id)
    say("journey", f"{journey_id} · thread {thread_id}")
    child = None
    try:
        child = await asyncio.create_subprocess_exec(
            sys.executable, __file__, "--worker", thread_id,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
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

        before = await _holds_for(client, journey_id)
        if len(before) != 1 or before[0]["booking_id"] != lost["booking_id"]:
            raise AssertionError("Expected exactly one committed hold before retry")
        checkpoint = await backend.saver.aget_tuple({"configurable": {"thread_id": thread_id}})
        values = checkpoint.checkpoint["channel_values"]
        intent = values["hold_intent"]
        if values.get("hold_id") or intent["booking_id"] != before[0]["booking_id"]:
            raise AssertionError("Expected the prepared intent without a hold acknowledgement")
        say("loss", "Aurora has the hold; the worker checkpoint has only the prepared intent")
        await asyncio.to_thread(check_denials, intent, thread_id)

        result = await _run_workflow(thread_id, resume=True)
        after = await _holds_for(client, journey_id)
        identity = lambda row: (row["hold_request_id"], row["booking_id"], row["hold_expires_at"])
        if len(after) != 1 or identity(before[0]) != identity(after[0]):
            raise AssertionError("Retry changed the request, booking, count, or original expiry")
        if result.get("workflow_status") != "resumed":
            raise AssertionError("Replacement workflow did not finish its resume")
        if result.get("hold_id") != before[0]["booking_id"]:
            raise AssertionError("Replacement worker did not receive the persisted hold")
        if result.get("resumed_from_checkpoint") != checkpoint.config["configurable"]["checkpoint_id"]:
            raise AssertionError("Replacement worker resumed a different checkpoint")
        executions = await client.execute(
            "SELECT status, worker_id FROM journey_executions "
            "WHERE thread_id = %s ORDER BY attempt", (thread_id,),
        )
        if [row["status"] for row in executions] != ["failed", "succeeded"]:
            raise AssertionError(f"Unexpected execution outcomes: {executions}")
        if executions[0]["worker_id"] == executions[1]["worker_id"]:
            raise AssertionError("Resume must use a different worker")
        say("resume", f"same request and booking {after[0]['booking_id']}; one persisted hold")
        say("expiry", f"original expiry retained: {after[0]['hold_expires_at']}")
        say("workers", "first execution failed; replacement resumed and succeeded")
        return 0
    finally:
        if child is not None and child.returncode is None:
            child.kill()
            await child.wait()
        await _purge(client, journey_id, thread_id)
        say("cleanup", "isolated rehearsal rows removed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(worker(args.worker) if args.worker else main()))
