"""Journey read API — the evidence behind Presenter proof.

`GET /api/journeys/{journey_id}` assembles a typed document from the tables
that already hold the facts. It has no workflow side effects.

Authorization runs on every read: the HTTP principal is resolved, the traveler
claim is authorized against it, and the journey's persisted owner is checked
inside the scoped session. A journey id grants nothing on its own.
"""

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

from botocore.exceptions import ClientError
from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.agentcore.errors import AgentCoreNotConfiguredError
from backend.agentcore.identity import get_agentcore_identity
from backend.agentcore.workflow_runtime import get_workflow_runtime
from backend.agents.phase_05_workflow.finished import run_finished as finished_nodes
from backend.agents.phase_05_workflow.graph import snapshot_key
from backend.db.journey_document import assemble_journey_document
from backend.db.journey_store import JOURNEY_SETTLED_SQL
from backend.db.rds_data_client import get_rds_data_client
from backend.http_auth import HttpPrincipal, authorize_traveler, require_http_principal

router = APIRouter(prefix="/api/journeys", tags=["journeys"])

ACTIVE_THREAD_SQL = "SELECT active_thread_id FROM journeys WHERE journey_id = %s"
LATEST_EXECUTION_SQL = """
SELECT execution_id, status,
       COALESCE((SELECT max(s.stopped_at) FROM workflow_session_stops s
                  WHERE s.journey_id = %s) > started_at, false) AS already_stopped
  FROM journey_executions
 WHERE thread_id = %s ORDER BY attempt DESC LIMIT 1
"""
LOCK_LATEST_EXECUTION_SQL = """
SELECT execution_id, status, started_at FROM journey_executions
 WHERE thread_id = %s ORDER BY attempt DESC LIMIT 1 FOR UPDATE
"""
NEWER_STOP_SQL = """
SELECT EXISTS (SELECT 1 FROM workflow_session_stops
                WHERE journey_id = %s AND stopped_at > %s::timestamptz) AS already_stopped
"""
SNAPSHOT_SQL = """
SELECT snapshot #>> '{data,state,execution_order,-1}' AS last_step,
       snapshot #>> '{data,state,status}' AS graph_status,
       snapshot #>> '{data,state,completed_nodes}' AS completed_nodes
  FROM workflow_snapshots WHERE session_id = %s AND storage_key = %s
 ORDER BY snapshot_seq DESC LIMIT 1
"""
CLOSE_LEASE_SQL = """
UPDATE journey_executions
   SET status = %s, ended_at = CURRENT_TIMESTAMP, lease_expires_at = NULL
 WHERE execution_id = %s AND status = 'running'
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
SELECT j.journey_id, j.status, j.snapshot_store, j.active_thread_id,
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


NEWER_ATTEMPT_NOTICE = (
    "A newer attempt started while the stop was in flight. The stop ended the shared "
    "Runtime session, so that attempt may have been stopped too. It was left to its lease, "
    "so a resume may be refused until the lease expires."
)
LOCK_TIMEOUT_DETAIL = (
    "The stop reached the Runtime, but recording it waited too long for another update "
    "to this journey. Try again in a few seconds."
)


@dataclass(frozen=True)
class StopTarget:
    """The thread and the execution a stop was issued against, and that execution's status."""

    thread_id: str
    execution_id: str
    status: str


@dataclass(frozen=True)
class Settled:
    """What the stop found: when it landed, the execution it closed, and any replacement."""

    during: str
    released_execution_id: Optional[str] = None
    newer_attempt: bool = False


def run_finished(snapshot: Optional[Dict[str, Any]]) -> bool:
    """Whether the run saved in this snapshot row finished: its synthesize node completed.

    It parses the row's column and applies the workflow's own rule
    (``phase_05_workflow.finished.run_finished``). A run whose held step was
    retried can end with a persisted status of ``failed`` after synthesize completed, so
    the graph status alone is not the rule.
    """
    raw = (snapshot or {}).get("completed_nodes")
    try:
        nodes = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return False
    return isinstance(nodes, list) and finished_nodes(nodes)


def _is_lock_timeout(exc: BaseException) -> bool:
    """Whether the Data API reported PostgreSQL 55P03 (lock_timeout expired).

    The Data API raises ``BadRequestException`` and carries the database's own message,
    ``canceling statement due to lock timeout``, with ``SQLState: 55P03``.
    """
    if not isinstance(exc, ClientError):
        return False
    error = exc.response.get("Error", {})
    message = str(error.get("Message", ""))
    return error.get("Code") == "BadRequestException" and (
        "SQLState: 55P03" in message or "due to lock timeout" in message
    )


async def _stop_target(client, journey_id: str, owner: str) -> StopTarget:
    """Return the thread and the execution to stop, refusing a journey with nothing live.

    The execution is the one live when the stop is issued; the settle step fences on it.
    """
    async with _scoped(client, owner) as tx:
        rows = await client.execute(ACTIVE_THREAD_SQL, (journey_id,), transaction_id=tx)
        thread_id = rows[0]["active_thread_id"] if rows else None
        if not thread_id:
            raise HTTPException(status_code=404, detail=f"Journey {journey_id} was not found.")
        latest = await client.execute(
            LATEST_EXECUTION_SQL, (journey_id, thread_id), transaction_id=tx
        )
        if not latest or latest[0]["status"] not in STOPPABLE:
            raise HTTPException(
                status_code=409,
                detail="This journey has no paused or running workflow session to stop.",
            )
        if latest[0]["already_stopped"]:
            raise HTTPException(status_code=409, detail="This session was already stopped.")
    return StopTarget(thread_id, latest[0]["execution_id"], latest[0]["status"])


async def _settle_execution(tx_execute, journey_id: str, target: StopTarget,
                            snapshot: Optional[Dict[str, Any]]) -> Settled:
    """Lock the thread's newest execution and close it as the stop found it.

    Returns what the stop caught. The newest execution is read here, under the lock,
    because a worker may have paused, finished or been replaced by a resume while the
    Runtime call was in flight. The lock also serialises concurrent stops: the one that
    gets the lock second sees the first stop's row and is refused with 409 before it
    records anything.

    When the newest execution is not ``target.execution_id``, a resume claimed a new one
    after the stop was issued. The stop still ended the shared Runtime session, so it is
    recorded against the original execution, the newer one is left to its lease, and the
    caller is told it may have been affected.
    A failed or abandoned execution that never finished its graph means the worker died
    mid-run: that is a ``running`` stop with nothing released.
    """
    rows = await tx_execute(LOCK_LATEST_EXECUTION_SQL, (target.thread_id,))
    latest = rows[0] if rows else None
    if latest:
        # A separate statement: the lock query's snapshot predates the wait, so a stop
        # committed by the lock's previous holder is only visible to a fresh statement.
        newer = await tx_execute(NEWER_STOP_SQL, (journey_id, latest["started_at"]))
        if newer[0]["already_stopped"]:
            raise HTTPException(status_code=409, detail="This session was already stopped.")
    if latest and latest["execution_id"] != target.execution_id:
        return Settled("waiting" if target.status == "paused" else "running", None, True)
    finished = run_finished(snapshot)
    if latest and latest["status"] == "running":
        outcome = "succeeded" if finished else "abandoned"
        await tx_execute(CLOSE_LEASE_SQL, (outcome, latest["execution_id"]))
        await tx_execute(JOURNEY_SETTLED_SQL, (outcome, journey_id, journey_id))
        return Settled("finished" if finished else "running", latest["execution_id"])
    if latest and latest["status"] != "paused" and finished:
        return Settled("finished")
    if latest and latest["status"] in ("failed", "abandoned"):
        return Settled("running")
    return Settled("waiting")


async def _record_stop(client, owner: str, journey_id: str, target: StopTarget,
                       stop: Any, *, requested_by: str) -> Dict[str, Any]:
    """Settle the execution the stop was issued against and record the stop in one transaction.

    Settling is safe once StopRuntimeSession has returned. The stopped worker
    can no longer save a snapshot, because the snapshot INSERT is fenced on a
    running execution. It can no longer place a hold, because the holds Lambda
    checks the lease FOR UPDATE. A hold that committed just before the stop is
    reused on resume through the same holdRequestId.

    Nothing here trusts the read made before the stop, except which execution it was
    issued against. The snapshot is read just before the lock, and a stopped worker
    cannot write another. A running execution whose snapshot shows the run finished
    only lost its release, so it closes as ``succeeded`` and the stop is ``finished``.
    Any other running execution is abandoned and the stop is ``running``; either way
    the journey takes the same status the workflow's own release would give it. Otherwise
    the run was waiting. A stop that finds a newer stop row after taking the lock is
    refused with 409.
    """
    async with _scoped(client, owner) as tx:
        async def tx_execute(sql, params):
            return await client.execute(sql, params, transaction_id=tx)

        snap = await tx_execute(SNAPSHOT_SQL, (target.thread_id, snapshot_key(target.thread_id)))
        last_step = snap[0]["last_step"] if snap else None
        settled = await _settle_execution(tx_execute, journey_id, target,
                                          snap[0] if snap else None)
        row = (await tx_execute(
            RECORD_STOP_SQL,
            (journey_id, target.thread_id, stop.runtime_session_id, stop.outcome,
             requested_by, settled.during, last_step, settled.released_execution_id),
        ))[0]
    recorded = {"stopped_at": row["stopped_at"], "stopped_during": settled.during,
                "last_step": last_step}
    if settled.newer_attempt:
        recorded.update(newer_attempt=True, notice=NEWER_ATTEMPT_NOTICE)
    return recorded


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
    owner = await authorize_traveler(principal, traveler_id)
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
                "snapshot_store": r["snapshot_store"],
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
    owner = await authorize_traveler(principal, traveler_id)
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
    run waiting for review, mid-run or already finished, and a mid-run stop
    releases the lease so a resume claims at once.

    Raises:
        HTTPException: 404 when the journey is not this traveler's or has no
            thread, 409 when the latest execution is neither running nor
            paused, its session was already stopped, or recording the stop timed
            out on a row lock (retry), 503 when the Runtime is not configured or
            the stop failed.
    """
    owner = await authorize_traveler(principal, traveler_id)
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
            client, owner, journey_id, target, stop, requested_by=principal.subject_id
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ClientError as exc:
        if _is_lock_timeout(exc):
            raise HTTPException(status_code=409, detail=LOCK_TIMEOUT_DETAIL) from exc
        raise
    return {
        "stopped": True,
        "runtime_session_id": stop.runtime_session_id,
        "outcome": stop.outcome,
        **recorded,
    }
