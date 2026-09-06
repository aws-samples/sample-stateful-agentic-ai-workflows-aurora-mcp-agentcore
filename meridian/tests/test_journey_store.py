"""Journey identity, thread ownership, and single-execution claiming.

The claim must be decided by the database, not by application logic reading
then writing, because two workers racing to resume is the exact scenario the
demo creates on purpose.
"""

from __future__ import annotations

import pytest

from backend.db.journey_store import (
    ExecutionClaim,
    bind_thread,
    claim_execution,
    create_journey,
    release_execution,
    renew_lease,
)


class FakeDB:
    """Records SQL and replays queued results.

    Models one PostgreSQL behaviour that a naive fake hides: a statement that
    raises poisons the surrounding transaction, so every later statement fails
    with 25P02. Code that recovers from a database error by running another
    query in the same transaction passes against a forgiving fake and fails
    against Aurora.
    """

    def __init__(self, results: list | None = None) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.results = results or []
        self.aborted = False

    async def execute(self, sql: str, params: tuple = (), **kwargs) -> list[dict]:
        normalized = " ".join(sql.split())
        if self.aborted:
            raise RuntimeError(
                "current transaction is aborted, commands ignored until end of "
                "transaction block; SQLState: 25P02"
            )
        self.calls.append((normalized, params))
        return self.results.pop(0) if self.results else []

    def statements(self) -> list[str]:
        return [sql for sql, _ in self.calls]


# ----------------------------------------------------------------- claiming


async def test_expired_leases_are_abandoned_before_claiming() -> None:
    db = FakeDB([[], [{"next_attempt": 2}], [{"execution_id": "exe_02"}]])
    await claim_execution(db, "jrn_1", "t1", "worker_02")
    first = db.statements()[0]
    assert "UPDATE journey_executions" in first
    assert "'abandoned'" in first
    assert "lease_expires_at <" in first


async def test_takeover_only_targets_expired_leases() -> None:
    db = FakeDB([[], [{"next_attempt": 2}], [{"execution_id": "exe_02"}]])
    await claim_execution(db, "jrn_1", "t1", "worker_02")
    assert "status = 'running'" in db.statements()[0]


async def test_claim_increments_the_attempt_number() -> None:
    db = FakeDB([[], [{"next_attempt": 3}], [{"execution_id": "exe_03"}]])
    claim = await claim_execution(db, "jrn_1", "t1", "worker_03")
    assert claim.attempt == 3
    assert claim.claimed is True


async def test_live_owner_yields_a_conflict_not_an_exception() -> None:
    """Losing the race must not be an error, and must not abort the transaction.

    The insert declines with ON CONFLICT DO NOTHING and returns no row. Letting
    the unique index raise instead would leave the transaction aborted, so the
    query that names the live owner would fail with 25P02.
    """
    db = FakeDB(
        [
            [],  # abandon expired
            [{"next_attempt": 2}],  # next attempt
            [],  # the claim declines: someone else is running
            [
                {
                    "execution_id": "exe_01",
                    "worker_id": "worker_01",
                    "lease_expires_at": "2026-09-06T02:14:32Z",
                }
            ],
        ]
    )
    claim = await claim_execution(db, "jrn_1", "t1", "worker_02")
    assert claim.claimed is False
    assert claim.conflict["execution_id"] == "exe_01"
    assert claim.conflict["worker_id"] == "worker_01"


async def test_the_claim_declines_rather_than_violating_the_index() -> None:
    """The insert must carry the partial index's own predicate.

    `ON CONFLICT (thread_id)` alone cannot infer a partial unique index and
    Postgres rejects the statement outright.
    """
    db = FakeDB([[], [{"next_attempt": 2}], [{"execution_id": "exe_02"}]])
    await claim_execution(db, "jrn_1", "t1", "worker_02")
    insert = next(s for s in db.statements() if "INSERT INTO journey_executions" in s)
    assert "ON CONFLICT (thread_id) WHERE status = 'running' DO NOTHING" in insert
    assert "RETURNING execution_id" in insert


async def test_an_unrelated_database_error_is_not_swallowed() -> None:
    """Only the one-running index converts into a conflict."""

    class Exploding(FakeDB):
        async def execute(self, sql, params=(), **kwargs):
            if "INSERT INTO journey_executions" in " ".join(sql.split()):
                self.aborted = True
                raise RuntimeError("connection reset by peer")
            return await super().execute(sql, params, **kwargs)

    db = Exploding([[], [{"next_attempt": 2}]])
    with pytest.raises(RuntimeError, match="connection reset"):
        await claim_execution(db, "jrn_1", "t1", "worker_02")


async def test_claim_records_the_claiming_worker() -> None:
    db = FakeDB([[], [{"next_attempt": 2}], [{"execution_id": "exe_02"}]])
    claim = await claim_execution(db, "jrn_1", "t1", "worker_02")
    assert claim.worker_id == "worker_02"
    insert = next(p for s, p in db.calls if "INSERT INTO journey_executions" in s)
    assert "worker_02" in insert


async def test_claim_never_creates_a_thread() -> None:
    db = FakeDB([[], [{"next_attempt": 2}], [{"execution_id": "exe_02"}]])
    await claim_execution(db, "jrn_1", "t1", "worker_02")
    assert not any("INSERT INTO journey_threads" in s for s in db.statements())


async def test_the_first_attempt_on_a_fresh_thread_is_one() -> None:
    db = FakeDB([[], [], [{"execution_id": "exe_01"}]])
    claim = await claim_execution(db, "jrn_1", "t1", "worker_01")
    assert claim.attempt == 1


def test_execution_claim_is_a_value_object() -> None:
    claim = ExecutionClaim(
        execution_id="exe_01", attempt=1, worker_id="w", claimed=True, conflict=None
    )
    assert claim.claimed and claim.conflict is None


# -------------------------------------------------------------------- lease


async def test_renewing_a_live_lease_reports_success() -> None:
    db = FakeDB([[{"execution_id": "exe_01"}]])
    assert await renew_lease(db, "exe_01") is True


async def test_renewing_a_lost_lease_reports_failure() -> None:
    """A worker whose slot was taken over must learn it, not keep working."""
    db = FakeDB([[]])
    assert await renew_lease(db, "exe_01") is False


async def test_release_records_the_terminal_status() -> None:
    db = FakeDB()
    await release_execution(db, "exe_01", "succeeded")
    sql, params = db.calls[0]
    assert "UPDATE journey_executions" in sql
    assert "lease_expires_at = NULL" in sql
    assert params == ("succeeded", "exe_01")


# ---------------------------------------------------------------- identity


async def test_create_journey_records_owner_and_backend() -> None:
    db = FakeDB()
    journey_id = await create_journey(db, "t1", "AuroraDataApiSaver")
    assert journey_id.startswith("jrn_")
    sql, params = db.calls[0]
    assert "INSERT INTO journeys" in sql
    assert params == (journey_id, "t1", "AuroraDataApiSaver")


async def test_binding_a_thread_points_the_journey_at_it() -> None:
    """Both halves must land: the thread row and the journey's active pointer."""
    db = FakeDB()
    await bind_thread(db, "jrn_1", "thread-a")
    statements = db.statements()
    assert any("INSERT INTO journey_threads" in s for s in statements)
    assert any("UPDATE journeys" in s and "active_thread_id" in s for s in statements)


async def test_rebinding_the_same_thread_is_idempotent() -> None:
    """Resume re-binds the thread it already owns; that must not error."""
    db = FakeDB()
    await bind_thread(db, "jrn_1", "thread-a")
    insert = next(s for s in db.statements() if "INSERT INTO journey_threads" in s)
    assert "ON CONFLICT" in insert
