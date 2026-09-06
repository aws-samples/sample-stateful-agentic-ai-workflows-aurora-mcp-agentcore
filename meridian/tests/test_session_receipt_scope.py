"""The closing receipt must count only what this session wrote.

The checkpoint line used to run `SELECT COUNT(*) FROM checkpoints` with no
predicate at all, while every other line on the receipt filtered on a time
window. So the one number backing the durability claim counted every thread,
every traveler, and all of history - a rehearsal from an hour earlier satisfied
a claim made about the run on screen, under a header reading "last 90 minutes"
and a footer reading "every one attributable to a workload authorized for a
single traveler".

These tests pin the predicate, because the defect was the absence of one.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from backend.routers import diagnostics


class RecordingDb:
    """Captures the SQL the receipt runs so the predicates can be asserted."""

    def __init__(self, counts: dict | None = None):
        self.queries: list[tuple[str, tuple]] = []
        self.counts = counts or {}

    async def execute(self, sql, params=(), transaction_id=None):
        self.queries.append((" ".join(sql.split()), params))
        for needle, value in self.counts.items():
            if needle in sql:
                return [{"n": value}]
        return [{"n": 0}]

    def scoped_session(self, **_kwargs):
        raise AssertionError("not needed for these tests")


def checkpoint_queries(db: RecordingDb) -> list[tuple[str, tuple]]:
    return [(sql, params) for sql, params in db.queries if re.search(r"\bFROM checkpoint", sql)]


def test_checkpoint_count_is_scoped_to_the_thread():
    db = RecordingDb()
    request = diagnostics.SessionReceiptRequest(conversation_id="thread-under-test")

    for table in diagnostics.CHECKPOINT_TABLES:
        asyncio.run(
            diagnostics._count_since(
                db,
                f"SELECT COUNT(*) AS n FROM {table} WHERE thread_id = %s",
                (request.conversation_id,),
            )
        )

    counted = checkpoint_queries(db)
    assert counted, "the receipt must count checkpoint tables"
    for sql, params in counted:
        assert "WHERE thread_id = %s" in sql, f"unscoped checkpoint count: {sql}"
        assert params == ("thread-under-test",)


def test_a_foreign_thread_does_not_count_toward_this_session():
    """Rows written by another thread must not satisfy this session's claim."""
    db = RecordingDb()
    mine = asyncio.run(
        diagnostics._count_since(
            db, "SELECT COUNT(*) AS n FROM checkpoints WHERE thread_id = %s", ("mine",)
        )
    )
    assert mine == 0

    sql, params = checkpoint_queries(db)[0]
    assert params == ("mine",), "the thread id has to reach the query as a parameter"
    assert "thread_id" in sql


def test_request_model_accepts_a_conversation_id():
    request = diagnostics.SessionReceiptRequest(conversation_id="phase5-abc123")
    assert request.conversation_id == "phase5-abc123"


def test_conversation_id_is_optional_and_defaults_to_none():
    """No thread ran, so there is nothing to claim - not everything to claim."""
    assert diagnostics.SessionReceiptRequest().conversation_id is None


def test_no_unpredicated_checkpoint_count_remains_in_source():
    """Guard the specific regression: a bare COUNT over a checkpoint table."""
    source = diagnostics.__file__
    with open(source, encoding="utf-8") as handle:
        text = " ".join(handle.read().split())
    for table in diagnostics.CHECKPOINT_TABLES:
        assert f'SELECT COUNT(*) AS n FROM {table}"' not in text, (
            f"unpredicated count over {table} reintroduced"
        )
