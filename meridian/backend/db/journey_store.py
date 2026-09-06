"""Journey identity, thread ownership, and execution claiming.

A journey outlives the threads and workers that serve it. Exactly one
execution runs per thread, decided by a partial unique index rather than by
application logic, so two workers racing to resume cannot both win.
"""

import uuid
from dataclasses import dataclass
from typing import Any, Optional

CREATE_JOURNEY_SQL = """
INSERT INTO journeys (journey_id, traveler_id, checkpoint_backend)
VALUES (%s, %s, %s)
"""

BIND_THREAD_SQL = """
INSERT INTO journey_threads (thread_id, journey_id)
VALUES (%s, %s)
ON CONFLICT (thread_id) DO NOTHING
"""

ACTIVATE_THREAD_SQL = """
UPDATE journeys
   SET active_thread_id = %s, updated_at = CURRENT_TIMESTAMP
 WHERE journey_id = %s
"""

ABANDON_EXPIRED_SQL = """
UPDATE journey_executions
   SET status = 'abandoned', ended_at = CURRENT_TIMESTAMP
 WHERE thread_id = %s
   AND status = 'running'
   AND lease_expires_at < CURRENT_TIMESTAMP
"""

NEXT_ATTEMPT_SQL = """
SELECT COALESCE(MAX(attempt), 0) + 1 AS next_attempt
  FROM journey_executions WHERE thread_id = %s
"""

CLAIM_SQL = """
INSERT INTO journey_executions
    (execution_id, journey_id, thread_id, attempt, worker_id, status,
     lease_expires_at)
VALUES (%s, %s, %s, %s, %s, 'running',
        CURRENT_TIMESTAMP + (%s || ' seconds')::interval)
RETURNING execution_id
"""

CURRENT_OWNER_SQL = """
SELECT execution_id, worker_id, lease_expires_at
  FROM journey_executions
 WHERE thread_id = %s AND status = 'running'
"""

RENEW_SQL = """
UPDATE journey_executions
   SET lease_expires_at = CURRENT_TIMESTAMP + (%s || ' seconds')::interval
 WHERE execution_id = %s AND status = 'running'
RETURNING execution_id
"""

RELEASE_SQL = """
UPDATE journey_executions
   SET status = %s, ended_at = CURRENT_TIMESTAMP, lease_expires_at = NULL
 WHERE execution_id = %s
"""


@dataclass
class ExecutionClaim:
    """The outcome of attempting to claim a thread's single running slot."""

    execution_id: Optional[str]
    attempt: int
    worker_id: str
    claimed: bool
    conflict: Optional[dict]


def _is_single_running_violation(error: Exception) -> bool:
    """Whether an error is the one-running-execution index rejecting a claim."""
    return "journey_executions_one_running" in str(error)


async def create_journey(db: Any, traveler_id: str, checkpoint_backend: str) -> str:
    """Open a journey for a traveler.

    Args:
        db: Data API client.
        traveler_id: The owner. Journey rows are traveler-scoped by RLS.
        checkpoint_backend: The backend serving this journey, recorded so a
            later resume can tell what actually persisted the state.

    Returns:
        The new journey id.
    """
    journey_id = f"jrn_{uuid.uuid4().hex[:12]}"
    await db.execute(CREATE_JOURNEY_SQL, (journey_id, traveler_id, checkpoint_backend))
    return journey_id


async def bind_thread(db: Any, journey_id: str, thread_id: str) -> None:
    """Attach a checkpoint thread to a journey and make it the active one.

    Idempotent: resume re-binds the thread the journey already owns.

    Args:
        db: Data API client.
        journey_id: The owning journey.
        thread_id: The LangGraph thread id.
    """
    await db.execute(BIND_THREAD_SQL, (thread_id, journey_id))
    await db.execute(ACTIVATE_THREAD_SQL, (thread_id, journey_id))


async def claim_execution(
    db: Any,
    journey_id: str,
    thread_id: str,
    worker_id: str,
    lease_seconds: int = 30,
) -> ExecutionClaim:
    """Claim the single running execution slot for a thread.

    Args:
        db: Data API client.
        journey_id: Owning journey.
        thread_id: The resume target. Must already be bound to the journey.
        worker_id: The worker claiming the slot.
        lease_seconds: How long the claim survives without a heartbeat.

    Returns:
        A claim. When ``claimed`` is False, ``conflict`` names the live owner.
        Expiry is applied as a state transition first, because PostgreSQL
        forbids a non-immutable predicate such as ``now()`` in an index.
    """
    await db.execute(ABANDON_EXPIRED_SQL, (thread_id,))
    rows = await db.execute(NEXT_ATTEMPT_SQL, (thread_id,))
    attempt = int(rows[0]["next_attempt"]) if rows else 1
    execution_id = f"exe_{uuid.uuid4().hex[:12]}"

    try:
        await db.execute(
            CLAIM_SQL,
            (
                execution_id,
                journey_id,
                thread_id,
                attempt,
                worker_id,
                str(lease_seconds),
            ),
        )
    except Exception as error:  # noqa: BLE001 - the index decides, not us
        if not _is_single_running_violation(error):
            raise
        owner = await db.execute(CURRENT_OWNER_SQL, (thread_id,))
        return ExecutionClaim(
            execution_id=None,
            attempt=attempt,
            worker_id=worker_id,
            claimed=False,
            conflict=dict(owner[0]) if owner else None,
        )

    return ExecutionClaim(
        execution_id=execution_id,
        attempt=attempt,
        worker_id=worker_id,
        claimed=True,
        conflict=None,
    )


async def renew_lease(db: Any, execution_id: str, lease_seconds: int = 30) -> bool:
    """Extend a running execution's lease.

    Args:
        db: Data API client.
        execution_id: The execution holding the slot.
        lease_seconds: The new lease duration from now.

    Returns:
        False when the execution no longer runs, which means the slot was
        taken over and this worker must stop.
    """
    rows = await db.execute(RENEW_SQL, (str(lease_seconds), execution_id))
    return bool(rows)


async def release_execution(db: Any, execution_id: str, status: str) -> None:
    """Mark an execution finished with a terminal status.

    Args:
        db: Data API client.
        execution_id: The execution to close.
        status: The terminal status, e.g. ``succeeded`` or ``failed``.
    """
    await db.execute(RELEASE_SQL, (status, execution_id))
