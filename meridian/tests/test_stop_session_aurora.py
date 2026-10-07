"""Stopping a journey's Runtime session: only your own journey, only while it runs or waits."""

import asyncio
import json
import os
import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException

from backend.agents.phase_05_workflow.graph import snapshot_key
from backend.agentcore.workflow_runtime import SessionStop, workflow_session_id
from backend.agentcore.identity import get_agentcore_identity
from backend.db.journey_document import assemble_journey_document
from backend.db.journey_store import bind_thread, claim_execution, create_journey
from backend.db.rds_data_client import get_rds_data_client
from backend.http_auth import HttpPrincipal
from backend.routers import journeys

pytestmark = pytest.mark.database

JORDAN = HttpPrincipal("test", "trv_meridian_demo", "test")

# Recording stopped_during='finished' needs migration 017's widened check constraint.
needs_017 = pytest.mark.skipif(
    os.environ.get("MERIDIAN_MIGRATION_017_APPLIED") != "1",
    reason="needs migration 017; set MERIDIAN_MIGRATION_017_APPLIED=1 once it is applied",
)


class Runtime:
    def __init__(self, outcome="stopped"):
        self.stops, self.outcome, self.while_stopping = [], outcome, None

    async def stop_session(self, traveler_id, thread_id):
        self.stops.append((traveler_id, thread_id))
        if self.while_stopping:
            await self.while_stopping(thread_id)
        return SessionStop(workflow_session_id(traveler_id, thread_id), self.outcome)


@pytest_asyncio.fixture
async def journey_with(monkeypatch):
    master = get_rds_data_client()
    made, runtime = [], Runtime()
    monkeypatch.setattr(journeys, "get_workflow_runtime", lambda: runtime)

    async def make(traveler="trv_meridian_demo", status="paused", completed=None,
                   graph_status=None):
        thread = f"stop-{uuid.uuid4().hex[:10]}"
        journey = await create_journey(master, traveler, "Aurora workflow_snapshots")
        await bind_thread(master, journey, thread)
        made.append((journey, thread))
        if status:
            await master.execute(
                "INSERT INTO journey_executions (execution_id, journey_id, thread_id, attempt, "
                "worker_id, status, lease_expires_at) VALUES "
                "(%s, %s, %s, 1, 'w', %s, CASE WHEN %s = 'running' "
                "THEN CURRENT_TIMESTAMP + interval '300 seconds' END)",
                (f"exec-{uuid.uuid4().hex[:12]}", journey, thread, status, status))
        if completed is not None:
            snapshot = {"data": {"state": {
                # Strands serializes completed_nodes from a set, so its order is arbitrary;
                # execution_order is the ordered list the last step must come from.
                "completed_nodes": completed[-1:] + completed[:-1],
                "execution_order": completed, "status": graph_status}}}
            await master.execute(
                "INSERT INTO workflow_snapshots (storage_key, session_id, traveler_id, snapshot) "
                "VALUES (%s, %s, %s, %s::jsonb)",
                (snapshot_key(thread), thread, traveler, json.dumps(snapshot)))
        return journey, thread

    yield make, runtime
    for journey, thread in made:
        await master.execute("DELETE FROM workflow_session_stops WHERE journey_id = %s", (journey,))
        await master.execute("DELETE FROM workflow_snapshots WHERE session_id = %s", (thread,))
        await master.execute("DELETE FROM journey_executions WHERE thread_id = %s", (thread,))
        await master.execute(
            "UPDATE journeys SET active_thread_id = NULL WHERE journey_id = %s", (journey,))
        await master.execute("DELETE FROM journey_threads WHERE thread_id = %s", (thread,))
        await master.execute("DELETE FROM journeys WHERE journey_id = %s", (journey,))


async def _stop_rows(journey):
    return await get_rds_data_client().execute(
        "SELECT thread_id, runtime_session_id, outcome, requested_by, stopped_during, "
        "last_step, released_execution_id FROM workflow_session_stops WHERE journey_id = %s",
        (journey,))


async def _statuses(thread):
    rows = await get_rds_data_client().execute(
        "SELECT execution_id, status, lease_expires_at FROM journey_executions "
        "WHERE thread_id = %s ORDER BY attempt", (thread,))
    return rows


async def test_a_paused_journey_stops_its_own_session_and_records_it(journey_with):
    make, runtime = journey_with
    journey, thread = await make(status="paused", completed=["intake", "retrieve"])
    reply = await journeys.stop_session(journey, JORDAN, None)
    assert reply["stopped"] is True and reply["outcome"] == "stopped"
    assert reply["stopped_during"] == "waiting" and reply["last_step"] == "retrieve"
    assert reply["runtime_session_id"] == workflow_session_id("trv_meridian_demo", thread)
    assert runtime.stops == [("trv_meridian_demo", thread)]
    assert await _stop_rows(journey) == [{
        "thread_id": thread, "runtime_session_id": reply["runtime_session_id"],
        "outcome": "stopped", "requested_by": "test", "stopped_during": "waiting",
        "last_step": "retrieve", "released_execution_id": None}]
    assert [r["status"] for r in await _statuses(thread)] == ["paused"]
    document = await assemble_journey_document(get_rds_data_client(), journey, "trv_meridian_demo")
    stops = document["session_stops"]
    assert stops["status"] == "observed" and stops["source"] == "workflow_session_stops"
    assert stops["items"][0]["outcome"] == "stopped"
    assert stops["items"][0]["runtime_session_id"] == reply["runtime_session_id"]
    assert stops["items"][0]["stopped_during"] == "waiting"
    assert stops["items"][0]["last_step"] == "retrieve"
    assert stops["items"][0]["stopped_at"]


async def test_the_journey_document_refuses_another_travelers_journey(journey_with):
    make, _ = journey_with
    journey, _ = await make(traveler="trv_demo_decoy", status="paused")
    await get_rds_data_client().execute(
        "INSERT INTO workflow_session_stops (journey_id, thread_id, runtime_session_id, outcome, "
        "requested_by, stopped_during) SELECT journey_id, active_thread_id, 'rt-wf-decoy', "
        "'stopped', 'test', 'waiting' FROM journeys WHERE journey_id = %s", (journey,))
    with pytest.raises(LookupError):
        await assemble_journey_document(get_rds_data_client(), journey, "trv_meridian_demo")


async def test_rls_hides_another_travelers_stop_row_from_a_scoped_read(journey_with):
    make, _ = journey_with
    master = get_rds_data_client()
    own, _ = await make(traveler="trv_meridian_demo", status="paused")
    decoy, _ = await make(traveler="trv_demo_decoy", status="paused")
    for journey in (own, decoy):
        await master.execute(
            "INSERT INTO workflow_session_stops (journey_id, thread_id, runtime_session_id, "
            "outcome, requested_by, stopped_during) SELECT journey_id, active_thread_id, "
            "'rt-wf-rls', 'stopped', 'test', 'waiting' FROM journeys WHERE journey_id = %s",
            (journey,))
    sql = "SELECT stop_id FROM workflow_session_stops WHERE journey_id = %s"
    async with master.scoped_session(
        traveler_id="trv_meridian_demo",
        agent_type="booking_agent",
        authorization=get_agentcore_identity().authorization_context(),
    ) as tx:
        visible_own = await master.execute(sql, (own,), transaction_id=tx)
        visible_decoy = await master.execute(sql, (decoy,), transaction_id=tx)
    assert len(visible_own) == 1
    assert visible_decoy == []
    assert len(await master.execute(sql, (decoy,))) == 1


async def test_a_journey_with_no_stop_says_so(journey_with):
    make, _ = journey_with
    journey, _ = await make(status="paused")
    document = await assemble_journey_document(get_rds_data_client(), journey, "trv_meridian_demo")
    assert document["session_stops"] == {
        "status": "unavailable", "reason": "No Runtime session was stopped for this journey."}


async def test_a_session_that_was_not_running_is_recorded_as_such(journey_with):
    make, runtime = journey_with
    runtime.outcome = "not_running"
    journey, _ = await make(status="paused")
    reply = await journeys.stop_session(journey, JORDAN, None)
    assert reply["outcome"] == "not_running"
    assert (await _stop_rows(journey))[0]["outcome"] == "not_running"


async def test_a_running_journey_is_released_at_once_and_can_be_claimed(journey_with):
    make, _ = journey_with
    journey, thread = await make(status="running", completed=["intake", "retrieve", "plan"])
    before = (await _statuses(thread))[0]
    reply = await journeys.stop_session(journey, JORDAN, None)
    assert reply["stopped_during"] == "running" and reply["last_step"] == "plan"
    after = await _statuses(thread)
    assert [r["status"] for r in after] == ["abandoned"]
    assert after[0]["lease_expires_at"] is None
    rows = await _stop_rows(journey)
    assert rows[0]["stopped_during"] == "running" and rows[0]["last_step"] == "plan"
    assert rows[0]["released_execution_id"] == before["execution_id"]
    fresh = await claim_execution(get_rds_data_client(), journey, thread, "resumer")
    assert fresh.claimed is True and fresh.attempt == 2


async def test_a_running_journey_with_no_snapshot_records_no_last_step(journey_with):
    make, _ = journey_with
    journey, _ = await make(status="running")
    reply = await journeys.stop_session(journey, JORDAN, None)
    assert reply["stopped_during"] == "running" and reply["last_step"] is None


async def test_another_travelers_journey_is_not_found_and_nothing_is_stopped(journey_with):
    make, runtime = journey_with
    journey, thread = await make(traveler="trv_demo_decoy", status="running")
    with pytest.raises(HTTPException) as caught:
        await journeys.stop_session(journey, JORDAN, None)
    assert caught.value.status_code == 404 and runtime.stops == []
    assert [r["status"] for r in await _statuses(thread)] == ["running"]


@pytest.mark.parametrize("status", ["succeeded", "failed", "abandoned", None])
async def test_only_a_running_or_paused_session_can_be_stopped(journey_with, status):
    make, runtime = journey_with
    journey, _ = await make(status=status)
    with pytest.raises(HTTPException) as caught:
        await journeys.stop_session(journey, JORDAN, None)
    assert caught.value.status_code == 409 and runtime.stops == []


async def test_a_failed_stop_call_is_503_and_leaves_the_lease_alone(journey_with):
    make, runtime = journey_with
    journey, thread = await make(status="running")

    async def broken(traveler_id, thread_id):
        raise RuntimeError("Stopping the workflow Runtime session failed: ThrottlingException")

    runtime.stop_session = broken
    with pytest.raises(HTTPException) as caught:
        await journeys.stop_session(journey, JORDAN, None)
    assert caught.value.status_code == 503
    assert [r["status"] for r in await _statuses(thread)] == ["running"]
    assert await _stop_rows(journey) == []


async def _master(sql, params=()):
    return await get_rds_data_client().execute(sql, params)


@needs_017
async def test_a_completed_run_whose_release_was_lost_is_closed_not_abandoned(journey_with):
    make, _ = journey_with
    journey, thread = await make(
        status="running", completed=["intake", "synthesize"], graph_status="completed")
    before = (await _statuses(thread))[0]
    reply = await journeys.stop_session(journey, JORDAN, None)
    assert reply["stopped_during"] == "finished" and reply["last_step"] == "synthesize"
    after = await _statuses(thread)
    assert [r["status"] for r in after] == ["succeeded"]
    assert after[0]["lease_expires_at"] is None
    rows = await _stop_rows(journey)
    assert rows[0]["stopped_during"] == "finished"
    assert rows[0]["released_execution_id"] == before["execution_id"]


@needs_017
async def test_a_worker_that_finishes_before_the_record_is_recorded_as_finished(journey_with):
    make, runtime = journey_with
    journey, thread = await make(
        status="running", completed=["intake", "synthesize"], graph_status="completed")

    async def worker_releases(thread_id):
        await _master(
            "UPDATE journey_executions SET status = 'succeeded', ended_at = CURRENT_TIMESTAMP, "
            "lease_expires_at = NULL WHERE thread_id = %s", (thread_id,))

    runtime.while_stopping = worker_releases
    reply = await journeys.stop_session(journey, JORDAN, None)
    assert reply["stopped_during"] == "finished"
    assert [r["status"] for r in await _statuses(thread)] == ["succeeded"]


async def test_a_run_that_pauses_while_the_stop_lands_is_recorded_as_waiting(journey_with):
    make, runtime = journey_with
    journey, thread = await make(status="running", completed=["intake", "retrieve"])

    async def worker_pauses(thread_id):
        await _master(
            "UPDATE journey_executions SET status = 'paused', lease_expires_at = NULL "
            "WHERE thread_id = %s", (thread_id,))

    runtime.while_stopping = worker_pauses
    reply = await journeys.stop_session(journey, JORDAN, None)
    assert reply["stopped_during"] == "waiting"
    assert [r["status"] for r in await _statuses(thread)] == ["paused"]
    assert (await _stop_rows(journey))[0]["released_execution_id"] is None


async def test_a_newer_claim_during_the_stop_is_the_execution_that_is_released(journey_with):
    make, runtime = journey_with
    journey, thread = await make(status="running", completed=["intake", "retrieve"])

    async def resume_claims_first(thread_id):
        await _master(
            "UPDATE journey_executions SET status = 'abandoned', lease_expires_at = NULL "
            "WHERE thread_id = %s", (thread_id,))
        await _master(
            "INSERT INTO journey_executions (execution_id, journey_id, thread_id, attempt, "
            "worker_id, status, lease_expires_at) VALUES (%s, %s, %s, 2, 'w2', 'running', "
            "CURRENT_TIMESTAMP + interval '300 seconds')",
            (f"exec-{uuid.uuid4().hex[:12]}", journey, thread_id))

    runtime.while_stopping = resume_claims_first
    reply = await journeys.stop_session(journey, JORDAN, None)
    assert reply["stopped_during"] == "running"
    rows = await _statuses(thread)
    assert [r["status"] for r in rows] == ["abandoned", "abandoned"]
    assert (await _stop_rows(journey))[0]["released_execution_id"] == rows[1]["execution_id"]
    fresh = await claim_execution(get_rds_data_client(), journey, thread, "resumer")
    assert fresh.claimed is True and fresh.attempt == 3


async def test_a_repeat_stop_is_refused_and_adds_no_row(journey_with):
    make, runtime = journey_with
    journey, _ = await make(status="paused", completed=["intake"])
    await journeys.stop_session(journey, JORDAN, None)
    with pytest.raises(HTTPException) as caught:
        await journeys.stop_session(journey, JORDAN, None)
    assert caught.value.status_code == 409
    assert caught.value.detail == "This session was already stopped."
    assert len(runtime.stops) == 1
    assert len(await _stop_rows(journey)) == 1


async def test_a_resume_after_a_stop_can_be_stopped_again(journey_with):
    make, runtime = journey_with
    journey, thread = await make(status="running", completed=["intake"])
    await journeys.stop_session(journey, JORDAN, None)
    fresh = await claim_execution(get_rds_data_client(), journey, thread, "resumer")
    assert fresh.claimed is True
    reply = await journeys.stop_session(journey, JORDAN, None)
    assert reply["stopped_during"] == "running"
    assert len(runtime.stops) == 2 and len(await _stop_rows(journey)) == 2


async def test_two_concurrent_stops_record_exactly_one_row(journey_with):
    make, runtime = journey_with
    journey, _ = await make(status="running", completed=["intake"])
    both_stopping = asyncio.Event()

    async def wait_for_the_other_stop(thread_id):
        if len(runtime.stops) == 2:
            both_stopping.set()
        await asyncio.wait_for(both_stopping.wait(), timeout=10)

    runtime.while_stopping = wait_for_the_other_stop
    results = await asyncio.gather(
        journeys.stop_session(journey, JORDAN, None),
        journeys.stop_session(journey, JORDAN, None),
        return_exceptions=True)
    refused = [r for r in results if isinstance(r, HTTPException)]
    stopped = [r for r in results if isinstance(r, dict)]
    assert len(stopped) == 1 and len(refused) == 1
    assert refused[0].status_code == 409
    assert refused[0].detail == "This session was already stopped."
    assert len(await _stop_rows(journey)) == 1


@pytest.mark.parametrize("worker_status", ["failed", "abandoned"])
async def test_a_run_that_died_while_the_stop_landed_is_recorded_as_running(
    journey_with, worker_status
):
    make, runtime = journey_with
    journey, thread = await make(status="running", completed=["intake", "retrieve"])

    async def worker_dies(thread_id):
        await _master(
            "UPDATE journey_executions SET status = %s, lease_expires_at = NULL "
            "WHERE thread_id = %s", (worker_status, thread_id))

    runtime.while_stopping = worker_dies
    reply = await journeys.stop_session(journey, JORDAN, None)
    assert reply["stopped_during"] == "running"
    assert (await _stop_rows(journey))[0]["released_execution_id"] is None
    assert [r["status"] for r in await _statuses(thread)] == [worker_status]
