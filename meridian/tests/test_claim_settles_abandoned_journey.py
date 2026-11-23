"""Abandoning an expired lease settles the journey with the rule the workflow's release uses."""

from backend.agents.phase_05_workflow.lease import JOURNEY_SETTLED_SQL as LEASE_SETTLED_SQL
from backend.db.journey_store import (
    ABANDON_EXPIRED_SQL,
    CLAIM_SQL,
    CURRENT_OWNER_SQL,
    JOURNEY_SETTLED_SQL,
    NEXT_ATTEMPT_SQL,
    claim_execution,
)


class FakeDb:
    def __init__(self, abandoned_journeys):
        self.abandoned_journeys = abandoned_journeys
        self.statements = []

    async def execute(self, sql, params=()):
        self.statements.append((sql, params))
        if sql == ABANDON_EXPIRED_SQL:
            return [{"journey_id": j} for j in self.abandoned_journeys]
        if sql == CURRENT_OWNER_SQL:
            return []
        if sql == NEXT_ATTEMPT_SQL:
            return [{"next_attempt": 2}]
        if sql == CLAIM_SQL:
            return [{"execution_id": params[0]}]
        return []


def test_the_abandon_settle_is_the_workflow_release_rule():
    assert JOURNEY_SETTLED_SQL == LEASE_SETTLED_SQL


def test_the_abandon_returns_the_journey_it_closed_an_execution_of():
    assert "RETURNING journey_id" in ABANDON_EXPIRED_SQL


def test_a_claim_starts_the_execution_at_the_insert_not_at_the_transaction_start():
    assert "clock_timestamp()" in CLAIM_SQL


async def test_an_abandoned_expired_lease_settles_its_journey_before_the_claim():
    db = FakeDb(["jrn_1"])
    await claim_execution(db, "jrn_1", "thread_1", "worker")
    sqls = [sql for sql, _ in db.statements]
    assert sqls.index(JOURNEY_SETTLED_SQL) < sqls.index(CLAIM_SQL)
    assert (JOURNEY_SETTLED_SQL, ("abandoned", "jrn_1", "jrn_1")) in db.statements


async def test_a_claim_with_nothing_to_abandon_settles_nothing():
    db = FakeDb([])
    await claim_execution(db, "jrn_1", "thread_1", "worker")
    assert JOURNEY_SETTLED_SQL not in [sql for sql, _ in db.statements]
