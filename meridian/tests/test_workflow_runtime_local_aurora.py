"""The Runtime entry, run locally as meridian_workflow against live Aurora and the live Gateway.

This proves the login's grants for real retrieval, the lease, scoped sessions and the hold
readback, which the mocked login test could not reach. The hold goes through the Gateway, the
holds Lambda and Cedar. The test purges everything it created, as the master role.
"""

import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path

import pytest

from backend.db.rds_data_client import get_rds_data_client
from scripts.kill_and_resume_proof import QUERY, _holds_for, _purge

pytestmark = pytest.mark.database

logger = logging.getLogger(__name__)

MERIDIAN = Path(__file__).resolve().parents[1]
TRAVELER = "trv_meridian_demo"
LEFTOVER_TABLES = (
    ("workflow_snapshots", "session_id"),
    ("journey_executions", "thread_id"),
    ("journey_threads", "thread_id"),
)


async def turn(thread_id: str, mode: str, session_id: str) -> list[dict]:
    payload = {"event": "workflow_turn", "mode": mode, "thread_id": thread_id,
               "traveler_id": TRAVELER, "query": QUERY, "travelers_count": 2}
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "tests.workflow_runtime_process", json.dumps(payload), session_id,
        cwd=MERIDIAN, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(child.communicate(), timeout=300)
    assert child.returncode == 0, err.decode()[-2000:]
    lines = [json.loads(line) for line in out.decode().splitlines() if line.startswith("{")]
    assert lines and lines[0] == {"current_user": "meridian_workflow"}, lines[:1]
    return lines[1:]


def final(events: list[dict]) -> dict:
    assert events[-1]["type"] == "result", events[-1]
    return events[-1]["state"]


def cedar_decisions(state: dict) -> list[str]:
    spans = [a for a in state["activities"] if a["title"] == "Workflow node: hold"]
    return [field["value"] for span in spans for field in span["telemetry"]["fields"]
            if field["label"] == "cedar_decision"]


async def leftovers(client, journey_id: str, thread_id: str) -> dict:
    counts = {}
    for table, column in LEFTOVER_TABLES:
        rows = await client.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE {column} = %s", (thread_id,))
        counts[table] = int(rows[0]["n"])
    counts["holds"] = len(await _holds_for(client, journey_id))
    rows = await client.execute(
        "SELECT COUNT(*) AS n FROM journeys WHERE journey_id = %s", (journey_id,))
    counts["journeys"] = int(rows[0]["n"])
    return counts


async def test_a_paused_review_resumes_on_another_session_and_holds_once():
    master = get_rds_data_client()
    thread_id = f"rtl-{uuid.uuid4().hex[:10]}"
    journey_id = ""
    try:
        started = final(await turn(thread_id, "start", "rt-local-session-one-padded-to-length"))
        rows = await master.execute(
            "SELECT journey_id FROM journey_threads WHERE thread_id = %s", (thread_id,))
        journey_id = rows[0]["journey_id"]
        assert started["workflow_status"] == "paused"
        assert await _holds_for(master, journey_id) == []

        resumed = final(await turn(thread_id, "resume", "rt-local-session-two-padded-to-length"))
        assert resumed["workflow_status"] == "resumed"
        holds = await _holds_for(master, journey_id)
        assert [h["status"] for h in holds] == ["held"]
        assert cedar_decisions(resumed) == ["allow"]
        assert started["worker_instance_id"] != resumed["worker_instance_id"]
        print(json.dumps({
            "start": started["workflow_status"], "resume": resumed["workflow_status"],
            "holds": [h["status"] for h in holds], "cedar": cedar_decisions(resumed),
            "workers": [started["worker_instance_id"], resumed["worker_instance_id"]],
        }))
    finally:
        if not journey_id:
            rows = await master.execute(
                "SELECT journey_id FROM journey_threads WHERE thread_id = %s", (thread_id,))
            journey_id = rows[0]["journey_id"] if rows else ""
        try:
            await _purge(master, journey_id, thread_id)
        except Exception:
            logger.exception("purge failed for thread %s journey %s", thread_id, journey_id)
    assert await leftovers(master, journey_id, thread_id) == {
        "workflow_snapshots": 0, "journey_executions": 0, "journey_threads": 0,
        "holds": 0, "journeys": 0}
