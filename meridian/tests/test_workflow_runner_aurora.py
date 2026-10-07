"""The Strands runner against live Aurora: snapshots and the lease survive a killed process."""

import asyncio
import json
import os
import signal
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from backend.agents.phase_05_workflow.runner import WorkflowCommand
from backend.agents.phase_05_workflow.service import build_workflow_runner
from backend.db.rds_data_client import get_rds_data_client
from tests.phase5_support import GatewayFake, fake_availability, fake_search

pytestmark = pytest.mark.database
MERIDIAN = Path(__file__).resolve().parents[1]
TRAVELER = "trv_meridian_demo"


@pytest_asyncio.fixture
async def thread():
    client = get_rds_data_client()
    thread_id = f"wfr-{uuid.uuid4().hex[:10]}"
    yield client, thread_id
    rows = await client.execute(
        "SELECT journey_id FROM journey_threads WHERE thread_id = %s", (thread_id,),
    )
    await client.execute("DELETE FROM workflow_snapshots WHERE session_id = %s", (thread_id,))
    await client.execute("DELETE FROM journey_executions WHERE thread_id = %s", (thread_id,))
    await client.execute(
        "UPDATE journeys SET active_thread_id = NULL WHERE active_thread_id = %s", (thread_id,),
    )
    await client.execute("DELETE FROM journey_threads WHERE thread_id = %s", (thread_id,))
    for row in rows:
        await client.execute("DELETE FROM journeys WHERE journey_id = %s", (row["journey_id"],))


async def worker(thread_id: str, mode: str) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        sys.executable, "-m", "tests.workflow_process", thread_id, mode,
        cwd=MERIDIAN, stdout=asyncio.subprocess.PIPE, env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )


async def next_event(process) -> dict:
    while line := await asyncio.wait_for(process.stdout.readline(), timeout=120):
        if line.startswith(b"{"):
            return json.loads(line)
    raise AssertionError("worker exited without an event")


async def test_a_killed_worker_is_replaced_and_the_hold_runs_once(thread):
    client, thread_id = thread
    first = await worker(thread_id, "start")
    assert (await next_event(first))["status"] == "paused"
    await first.wait()

    holder = await worker(thread_id, "hold")
    paused = await next_event(holder)
    assert paused["event"] == "paused"
    os.kill(holder.pid, signal.SIGKILL)
    await holder.wait()

    snapshots = await client.execute(
        "SELECT status, worker_id FROM workflow_snapshots "
        "WHERE session_id = %s ORDER BY snapshot_seq",
        (thread_id,),
    )
    assert snapshots[-1]["status"] == "interrupted"

    runner = build_workflow_runner(
        search_fn=fake_search, availability_fn=fake_availability,
        gateway_call=GatewayFake(), lease_seconds=15, heartbeat_seconds=3,
    )
    runner._nodes._prepare_governed_hold = AsyncMock(return_value=("jrn_live", 400000))
    runner._nodes._booking_status = AsyncMock(return_value=None)
    command = WorkflowCommand(
        query="Resume workflow", traveler_id=TRAVELER, thread_id=thread_id, resume=True,
    )
    for _ in range(30):
        try:
            result = await runner.run(command)
            break
        except Exception as exc:  # the dead worker's lease must expire first
            if "already running" not in str(exc):
                raise
            await asyncio.sleep(2)
    else:
        raise AssertionError("the dead worker's lease never cleared")

    assert result["workflow_status"] == "resumed"
    assert result["resumed_after_restart"] is True
    executions = await client.execute(
        "SELECT status FROM journey_executions WHERE thread_id = %s ORDER BY attempt", (thread_id,),
    )
    assert [row["status"] for row in executions] == ["paused", "abandoned", "succeeded"]
    hold_spans = [a for a in result["activities"] if a["title"] == "Workflow node: hold"]
    assert len(hold_spans) == 1, "the replacement must not run the committed hold again"
    count = await client.execute(
        "SELECT COUNT(*) AS n FROM workflow_snapshots WHERE session_id = %s", (thread_id,),
    )
    assert int(count[0]["n"]) >= 7
