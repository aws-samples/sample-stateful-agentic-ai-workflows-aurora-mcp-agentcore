"""Journey read API — the evidence behind Presenter proof.

`GET /api/journeys/{journey_id}` assembles a typed document from the tables
that already hold the facts. It has no workflow side effects.

Authorization runs on every read: the HTTP principal is resolved, the traveler
claim is authorized against it, and the journey's persisted owner is checked
inside the scoped session. A journey id grants nothing on its own.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.agentcore.errors import AgentCoreNotConfiguredError
from backend.agentcore.identity import get_agentcore_identity
from backend.agentcore.workflow_runtime import get_workflow_runtime
from backend.agents.phase_05_workflow.graph import snapshot_key
from backend.db.journey_document import assemble_journey_document
from backend.db.rds_data_client import get_rds_data_client
from backend.http_auth import HttpPrincipal, authorize_traveler, require_http_principal

router = APIRouter(prefix="/api/journeys", tags=["journeys"])

ACTIVE_THREAD_SQL = "SELECT active_thread_id FROM journeys WHERE journey_id = %s"
LATEST_EXECUTION_SQL = """
SELECT execution_id, status FROM journey_executions
 WHERE thread_id = %s ORDER BY attempt DESC LIMIT 1
"""
LAST_STEP_SQL = """
SELECT snapshot #>> '{data,state,execution_order,-1}' AS last_step
  FROM workflow_snapshots WHERE session_id = %s AND storage_key = %s
 ORDER BY snapshot_seq DESC LIMIT 1
"""
RELEASE_LEASE_SQL = """
UPDATE journey_executions
   SET status = 'abandoned', ended_at = CURRENT_TIMESTAMP, lease_expires_at = NULL
 WHERE execution_id = %s AND status = 'running'
RETURNING execution_id
"""
RECORD_STOP_SQL = """
INSERT INTO workflow_session_stops
    (journey_id, thread_id, runtime_session_id, outcome, requested_by,
     stopped_during, last_step, released_execution_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
RETURNING stopped_at::TEXT AS stopped_at
"""
STOPPABLE = frozenset({"running", "paused"})

LATEST_JOURNEYS_SQL = """
SELECT j.journey_id, j.status, j.checkpoint_backend, j.active_thread_id,
       j.created_at, j.updated_at,
       (SELECT count(*) FROM journey_executions e WHERE e.journey_id = j.journey_id)
           AS execution_count
  FROM journeys j
 WHERE j.traveler_id = %s
   AND (%s::text IS NULL OR j.active_thread_id = %s)
 ORDER BY j.created_at DESC
 LIMIT %s
"""


def _scoped(client, owner: str):
    return client.scoped_session(
        traveler_id=owner,
        agent_type="booking_agent",
        authorization=get_agentcore_identity().authorization_context(),
    )


@dataclass(frozen=True)
class StopTarget:
    """What a stop acts on, read under RLS before the Runtime is touched."""

    thread_id: str
    execution_id: str
    status: str


async def _stop_target(client, journey_id: str, owner: str) -> StopTarget:
    async with _scoped(client, owner) as tx:
        rows = await client.execute(ACTIVE_THREAD_SQL, (journey_id,), transaction_id=tx)
        thread_id = rows[0]["active_thread_id"] if rows else None
        if not thread_id:
            raise HTTPException(status_code=404, detail=f"Journey {journey_id} was not found.")
        latest = await client.execute(LATEST_EXECUTION_SQL, (thread_id,), transaction_id=tx)
        if not latest or latest[0]["status"] not in STOPPABLE:
            raise HTTPException(
                status_code=409,
                detail="This journey has no paused or running workflow session to stop.",
            )
    return StopTarget(thread_id, latest[0]["execution_id"], latest[0]["status"])


async def _record_stop(client, owner: str, journey_id: str, target: StopTarget,
                       stop: Any, requested_by: str) -> Dict[str, Any]:
    """Read the last step, release a running lease and record the stop in one transaction.

    Releasing is safe once StopRuntimeSession has returned. The stopped worker
    can no longer save a snapshot, because the snapshot INSERT is fenced on a
    running execution. It can no longer place a hold, because the holds Lambda
    checks the lease FOR UPDATE. A hold that committed just before the stop is
    reused on resume through the same holdRequestId. The step is read here,
    after the stop, so it is the last one the run saved.

    ``stopped_during`` follows what the UPDATE did, not what the earlier read
    saw: if the run paused between the read and the update, the fenced UPDATE
    matches no row, and the stop is recorded as ``waiting``, not as a mid-run stop.
    """
    async with _scoped(client, owner) as tx:
        step = await client.execute(
            LAST_STEP_SQL, (target.thread_id, snapshot_key(target.thread_id)),
            transaction_id=tx,
        )
        last_step = step[0]["last_step"] if step else None
        released = []
        if target.status == "running":
            released = await client.execute(
                RELEASE_LEASE_SQL, (target.execution_id,), transaction_id=tx
            )
        during = "running" if released else "waiting"
        row = (await client.execute(
            RECORD_STOP_SQL,
            (journey_id, target.thread_id, stop.runtime_session_id, stop.outcome,
             requested_by, during, last_step,
             released[0]["execution_id"] if released else None),
            transaction_id=tx,
        ))[0]
    return {"stopped_at": row["stopped_at"], "stopped_during": during, "last_step": last_step}


@router.get("")
async def list_journeys(
    principal: HttpPrincipal = Depends(require_http_principal),
    traveler_id: Optional[str] = Query(default=None, max_length=50),
    limit: int = Query(default=10, ge=1, le=50),
    thread_id: Optional[str] = Query(default=None, min_length=1, max_length=200),
) -> Dict[str, Any]:
    """List the caller's journeys, newest first.

    The shell needs a journey id before it can read a document, and a demo
    machine should not have to be told one by hand.
    """
    owner = authorize_traveler(principal, traveler_id)
    client = get_rds_data_client()
    try:
        async with _scoped(client, owner) as tx:
            rows = await client.execute(
                LATEST_JOURNEYS_SQL, (owner, thread_id, thread_id, limit), transaction_id=tx
            )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {
        "traveler_id": owner,
        "journeys": [
            {
                "journey_id": r["journey_id"],
                "status": r["status"],
                "checkpoint_backend": r["checkpoint_backend"],
                "active_thread_id": r["active_thread_id"],
                "execution_count": int(r["execution_count"]),
                "created_at": str(r["created_at"]),
                "updated_at": str(r["updated_at"]),
            }
            for r in rows
        ],
    }


@router.get("/{journey_id}")
async def read_journey(
    journey_id: str,
    principal: HttpPrincipal = Depends(require_http_principal),
    traveler_id: Optional[str] = Query(default=None, max_length=50),
) -> Dict[str, Any]:
    """Assemble the evidence document for one journey.

    Raises:
        HTTPException: 404 when no such journey is visible to this traveler,
            403 when the journey is owned by someone else.
    """
    owner = authorize_traveler(principal, traveler_id)
    try:
        return await assemble_journey_document(
            get_rds_data_client(), journey_id, owner
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc


@router.post("/{journey_id}/stop-session")
async def stop_session(
    journey_id: str,
    principal: HttpPrincipal = Depends(require_http_principal),
    traveler_id: Optional[str] = Query(default=None, max_length=50),
) -> Dict[str, Any]:
    """Stop the AgentCore Runtime session running this journey's workflow.

    The session id is derived from the journey's own active thread, read under
    RLS, never taken from the request, so a caller can stop only their own
    journey's session. The stop is recorded in Aurora with whether it caught the
    run waiting for review or mid-run, and a mid-run stop releases the lease so
    a resume claims at once.

    Raises:
        HTTPException: 404 when the journey is not this traveler's or has no
            thread, 409 when the latest execution is neither running nor
            paused, 503 when the Runtime is not configured or the stop failed.
    """
    owner = authorize_traveler(principal, traveler_id)
    client = get_rds_data_client()
    try:
        target = await _stop_target(client, journey_id, owner)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    try:
        stop = await get_workflow_runtime().stop_session(owner, target.thread_id)
    except (AgentCoreNotConfiguredError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc).splitlines()[0]) from exc
    try:
        recorded = await _record_stop(
            client, owner, journey_id, target, stop, principal.subject_id
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {
        "stopped": True,
        "runtime_session_id": stop.runtime_session_id,
        "outcome": stop.outcome,
        **recorded,
    }
