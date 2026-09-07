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
RETURNING journey_id
"""

ACTIVATE_THREAD_SQL = """
UPDATE journeys
   SET active_thread_id = %s, updated_at = CURRENT_TIMESTAMP
 WHERE journey_id = %s
"""

JOURNEY_FOR_THREAD_SQL = """
SELECT journey_id FROM journey_threads WHERE thread_id = %s
"""

ABANDON_EXPIRED_SQL = """
UPDATE journey_executions
   SET status = 'abandoned', ended_at = CURRENT_TIMESTAMP
 WHERE execution_id IN (
     SELECT execution_id FROM journey_executions
      WHERE thread_id = %s AND status = 'running'
        AND lease_expires_at <= CURRENT_TIMESTAMP
      FOR UPDATE SKIP LOCKED
 )
"""

NEXT_ATTEMPT_SQL = """
SELECT COALESCE(MAX(attempt), 0) + 1 AS next_attempt
  FROM journey_executions WHERE thread_id = %s
"""

# DO NOTHING rather than letting the index raise. A unique violation aborts the
# whole transaction in PostgreSQL, so the follow-up query naming the live owner
# would fail with 25P02 and the caller would see a driver error instead of a
# conflict. An empty RETURNING is the conflict.
CLAIM_SQL = """
INSERT INTO journey_executions
    (execution_id, journey_id, thread_id, attempt, worker_id, status,
     lease_expires_at)
VALUES (%s, %s, %s, %s, %s, 'running',
        CURRENT_TIMESTAMP + (%s || ' seconds')::interval)
ON CONFLICT (thread_id) WHERE status = 'running' DO NOTHING
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
   AND lease_expires_at > CURRENT_TIMESTAMP
RETURNING execution_id
"""

RELEASE_SQL = """
UPDATE journey_executions
   SET status = %s, ended_at = CURRENT_TIMESTAMP, lease_expires_at = NULL
 WHERE execution_id = %s AND status = 'running'
"""


class ExecutionLeaseLostError(RuntimeError):
    """This execution no longer owns the right to change business state."""


class ScopedDb:
    """Route journey statements through an already-open scoped transaction.

    The journey tables are behind RLS, so every statement has to run inside the
    transaction that set ``app.current_traveler_id``. The Data API client takes
    the transaction as a keyword argument; this binds it once.
    """

    def __init__(self, client: Any, transaction_id: str) -> None:
        self.client = client
        self.transaction_id = transaction_id

    async def execute(self, sql: str, params: tuple = (), **kwargs) -> Any:
        return await self.client.execute(
            sql, params, transaction_id=self.transaction_id
        )


@dataclass
class ExecutionClaim:
    """The outcome of attempting to claim a thread's single running slot."""

    execution_id: Optional[str]
    attempt: int
    worker_id: str
    claimed: bool
    conflict: Optional[dict]


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
    if await journey_for_thread(db, thread_id) != journey_id:
        raise PermissionError("The workflow thread is already bound to another journey")
    await db.execute(ACTIVATE_THREAD_SQL, (thread_id, journey_id))


async def journey_for_thread(db: Any, thread_id: str) -> Optional[str]:
    """Return the journey a thread already belongs to, if any.

    Reads through RLS, so a thread owned by another traveler is invisible here
    and reads as unbound rather than as someone else's journey.

    Args:
        db: Data API client, inside a traveler-scoped session.
        thread_id: The LangGraph thread id.

    Returns:
        The journey id, or None when the thread is not bound.
    """
    rows = await db.execute(JOURNEY_FOR_THREAD_SQL, (thread_id,))
    return str(rows[0]["journey_id"]) if rows else None


async def ensure_journey(
    db: Any, traveler_id: str, thread_id: str, checkpoint_backend: str
) -> str:
    """Return the thread's journey, creating and binding one if it has none.

    Args:
        db: Data API client, inside a traveler-scoped session.
        traveler_id: The owner.
        thread_id: The LangGraph thread id.
        checkpoint_backend: The backend serving this journey.

    Returns:
        The journey id the thread is bound to.
    """
    # Serialize the first binding, including the interval before any checkpoint
    # exists. RLS then verifies the winning binding belongs to this traveler.
    await db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (thread_id,))
    existing = await journey_for_thread(db, thread_id)
    if existing:
        return existing

    journey_id = await create_journey(db, traveler_id, checkpoint_backend)
    await bind_thread(db, journey_id, thread_id)
    return journey_id


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

    Raises:
        Exception: Any database error other than losing the race. Losing is
            not an error: the insert declines and returns no row.
    """
    await db.execute(ABANDON_EXPIRED_SQL, (thread_id,))
    # A killed Data API worker may leave a short-lived transaction lock.
    # Do not wait on its unique-index entry while Aurora rolls that back.
    owner = await db.execute(CURRENT_OWNER_SQL, (thread_id,))
    if owner:
        return ExecutionClaim(None, 0, worker_id, False, dict(owner[0]))
    rows = await db.execute(NEXT_ATTEMPT_SQL, (thread_id,))
    attempt = int(rows[0]["next_attempt"]) if rows else 1
    execution_id = f"exe_{uuid.uuid4().hex[:12]}"

    claimed = await db.execute(
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
    if not claimed:
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
