"""meridian_workflow against the live cluster: what the Runtime's login can and cannot do."""

import asyncio
import json
import os
import sys
import uuid

import pytest
import pytest_asyncio

from backend.agents.phase_05_workflow.lease import AuroraLeaseStore
from backend.agents.phase_05_workflow.snapshot_storage import AuroraSnapshotStorage
from backend.db.journey_store import (
    ExecutionLeaseLostError,
    bind_thread,
    claim_execution,
    create_journey,
)
from backend.db.rds_data_client import RDSDataClient, get_rds_data_client

pytestmark = pytest.mark.database

TRAVELER = "trv_meridian_demo"
DECOY = "trv_demo_decoy"


def key(thread: str) -> str:
    return f"session/{thread}/scopes/multiAgent/phase5/snapshots/snapshot_latest.json"


@pytest.fixture
def login() -> RDSDataClient:
    secret = os.environ.get("AURORA_WORKFLOW_SECRET_ARN")
    if not secret:
        pytest.fail(
            "AURORA_WORKFLOW_SECRET_ARN is not set: run scripts/provision_workflow_login.py")
    return RDSDataClient(secret_arn=secret)


@pytest_asyncio.fixture
async def made():
    master = get_rds_data_client()
    threads: list[tuple[str, str]] = []
    yield threads
    for thread_id, journey_id in threads:
        for sql in (
            "DELETE FROM workflow_snapshots WHERE session_id = %s",
            "DELETE FROM journey_executions WHERE thread_id = %s",
            "UPDATE journeys SET active_thread_id = NULL WHERE active_thread_id = %s",
            "DELETE FROM journey_threads WHERE thread_id = %s",
        ):
            await master.execute(sql, (thread_id,))
        await master.execute("DELETE FROM journeys WHERE journey_id = %s", (journey_id,))


async def _thread(made, traveler: str) -> str:
    master = get_rds_data_client()
    thread = f"login-{uuid.uuid4().hex[:10]}"
    journey = await create_journey(master, traveler, "Aurora workflow_snapshots")
    await bind_thread(master, journey, thread)
    made.append((thread, journey))
    return thread


async def test_the_login_claims_writes_and_reads_its_travelers_snapshot(login, made):
    thread = await _thread(made, TRAVELER)
    lease = AuroraLeaseStore(login)
    journey = await lease.ensure_journey(TRAVELER, thread)
    claim = await lease.claim(TRAVELER, journey, thread, "worker-login", 30)
    assert claim.claimed
    storage = AuroraSnapshotStorage(login, session_id=thread, traveler_id=TRAVELER,
                                    execution_id=claim.execution_id, worker_id="worker-login")
    await storage.write(key(thread), b'{"data": {"state": {"status": "executing"}}}')
    assert json.loads(await storage.read(key(thread)))["data"]["state"]["status"] == "executing"
    assert await storage.list("session/") == [key(thread)]


async def test_the_login_cannot_see_another_travelers_snapshot(login, made):
    thread = await _thread(made, DECOY)
    await get_rds_data_client().execute(
        "INSERT INTO workflow_snapshots (storage_key, session_id, traveler_id, snapshot) "
        "VALUES (%s, %s, %s, '{}'::jsonb)", (key(thread), thread, DECOY))
    jordan = AuroraSnapshotStorage(
        login, session_id=thread, traveler_id=TRAVELER, execution_id=None)
    assert await jordan.read(key(thread)) is None
    assert await jordan.list("") == []


async def test_a_real_execution_of_another_travelers_thread_cannot_be_written_to(login, made):
    """The lease check refuses it, because RLS hides the decoy's execution from the login.

    The execution is real and running. With TRAVELER pinned, the fence's subquery on
    journey_executions sees no row, so the fenced INSERT returns nothing.
    """
    thread = await _thread(made, DECOY)
    master = get_rds_data_client()
    journey = made[-1][1]
    claim = await claim_execution(master, journey, thread, "worker-decoy", 30)
    assert claim.claimed
    forged = AuroraSnapshotStorage(login, session_id=thread, traveler_id=TRAVELER,
                                   execution_id=claim.execution_id, worker_id="worker-login")
    with pytest.raises(ExecutionLeaseLostError):
        await forged.write(key(thread), b'{"data": {}}')
    rows = await master.execute(
        "SELECT COUNT(*) AS n FROM workflow_snapshots WHERE session_id = %s", (thread,))
    assert rows[0]["n"] == 0


async def test_the_insert_policy_refuses_a_row_stamped_for_another_traveler(login, made):
    """Only the INSERT policy's WITH CHECK can stop this: no lease fence is involved."""
    thread = await _thread(made, TRAVELER)
    tx = login.begin_transaction()
    try:
        await login.execute(
            "SELECT set_config('app.current_traveler_id', %s, true)", (TRAVELER,),
            transaction_id=tx)
        with pytest.raises(Exception, match="row-level security"):
            await login.execute(
                "INSERT INTO workflow_snapshots (storage_key, session_id, traveler_id, snapshot) "
                "VALUES (%s, %s, %s, '{}'::jsonb)", (key(thread), thread, DECOY),
                transaction_id=tx)
    finally:
        login.rollback_transaction(tx)
    rows = await get_rds_data_client().execute(
        "SELECT COUNT(*) AS n FROM workflow_snapshots WHERE session_id = %s", (thread,))
    assert rows[0]["n"] == 0


@pytest.mark.parametrize("sql", [
    "UPDATE workflow_snapshots SET worker_id = worker_id WHERE false",
    "DELETE FROM workflow_snapshots WHERE false",
    "UPDATE bookings SET status = status WHERE false",
])
async def test_the_login_cannot_rewrite_history_or_bookings(login, sql):
    with pytest.raises(Exception, match="permission denied"):
        await login.execute(sql)


async def test_a_resume_of_another_travelers_thread_reads_as_nothing_to_resume(login, made):
    from backend.agents.phase_05_workflow.runner import WorkflowCommand, WorkflowConflictError
    from backend.agents.phase_05_workflow.service import build_workflow_runner
    thread = await _thread(made, DECOY)
    await get_rds_data_client().execute(
        "INSERT INTO workflow_snapshots (storage_key, session_id, traveler_id, snapshot) "
        "VALUES (%s, %s, %s, '{\"data\": {\"state\": {}}}'::jsonb)", (key(thread), thread, DECOY))
    runner = build_workflow_runner(client=login)
    with pytest.raises(WorkflowConflictError, match="no pending checkpoint"):
        await runner.run(WorkflowCommand(query="resume", traveler_id=TRAVELER, thread_id=thread,
                                         resume=True))


async def test_the_workflow_pauses_and_resumes_under_the_login(made):
    # The subprocess sets AURORA_SECRET_ARN to the login's secret before importing
    # backend, so every client in the run, the lease included, is meridian_workflow.
    # _prepare_governed_hold and _booking_status are mocked there, so the grants they
    # need are proven later by the local Runtime run (Task 9).
    thread = await _thread(made, TRAVELER)
    workers = set()
    for mode, expected in (("start", "paused"), ("resume", "resumed")):
        child = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "tests.workflow_login_process", thread, mode,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(child.communicate(), timeout=180)
        assert child.returncode == 0, err.decode()[-2000:]
        lines = [json.loads(line) for line in out.decode().splitlines() if line.startswith("{")]
        assert {"current_user": "meridian_workflow"} in lines
        assert lines[-1]["workflow_status"] == expected
        workers.add(lines[-1]["worker"])
    rows = await get_rds_data_client().execute(
        "SELECT DISTINCT worker_id FROM workflow_snapshots WHERE session_id = %s", (thread,))
    assert rows
    assert {row["worker_id"] for row in rows} == workers
