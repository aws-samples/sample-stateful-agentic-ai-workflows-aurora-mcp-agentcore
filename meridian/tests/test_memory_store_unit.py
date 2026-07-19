"""Unit tests for ``backend.memory.store``.

These tests exercise the SQL the MemoryStore emits without touching Aurora —
they record every call to the DB client and assert on shape + ordering.
"""

from __future__ import annotations

import asyncio
from typing import Any, List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

from backend.memory.store import MemoryStore


class _RecordingDb:
    """Async-shaped stub that records every ``execute`` call.

    Each call is appended to ``self.calls`` as ``(sql, params, transaction_id)``.
    ``execute`` returns whatever the most recent ``responses`` entry holds —
    use ``self.queue(rows)`` to set a one-shot response.
    """

    def __init__(self) -> None:
        self.calls: List[Tuple[str, Tuple[Any, ...], Optional[str]]] = []
        self._queue: List[List[dict]] = []

    def queue(self, rows: List[dict]) -> None:
        self._queue.append(rows)

    async def execute(self, sql: str, params: Tuple[Any, ...] = (), *, transaction_id: Optional[str] = None):
        self.calls.append((sql, params, transaction_id))
        if self._queue:
            return self._queue.pop(0)
        return []


@pytest.fixture
def store(monkeypatch):
    """Build a MemoryStore with a recording DB and a no-op embedding service."""
    db = _RecordingDb()

    # The real MemoryStore.__init__ pulls live clients. Bypass them.
    s = MemoryStore.__new__(MemoryStore)
    s.db = db  # type: ignore[attr-defined]
    s.embeddings = MagicMock()  # type: ignore[attr-defined]
    return s, db


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


class TestEnsureTravelerExists:
    """Regression coverage for the conversations_traveler_id_fkey violation."""

    def test_emits_upsert_into_travelers_table(self, store):
        s, db = store
        _run(s.ensure_traveler_exists("trv_meridian_demo", transaction_id="tx-1"))
        assert len(db.calls) == 1
        sql, params, tx = db.calls[0]
        assert "INSERT INTO travelers" in sql
        assert "ON CONFLICT" in sql
        assert "DO NOTHING" in sql
        assert params[0] == "trv_meridian_demo"
        # Placeholder name/email must be deterministic and unique per id.
        assert params[1] == "Traveler trv_meridian_demo"
        assert params[2] == "trv_meridian_demo@meridian.demo"
        assert tx == "tx-1"


class TestGetOrCreateConversation:
    def test_creating_a_new_conversation_first_ensures_traveler(self, store):
        """``get_or_create_conversation`` MUST upsert the traveler before the conversation insert.

        Otherwise Aurora rejects the conversation INSERT with a
        ``conversations_traveler_id_fkey`` violation, which is exactly the
        runtime error we shipped before this fix.
        """
        s, db = store
        # No prior conversation row, so lookup is skipped (conversation_id=None).
        conv_id = _run(
            s.get_or_create_conversation("trv_meridian_demo", conversation_id=None, transaction_id="tx-2")
        )
        # The order matters: travelers INSERT must come before conversations INSERT.
        assert len(db.calls) == 2
        first_sql, _, _ = db.calls[0]
        second_sql, _, _ = db.calls[1]
        assert "INSERT INTO travelers" in first_sql
        assert "INSERT INTO conversations" in second_sql
        assert conv_id.startswith("conv_")

    def test_reusing_existing_conversation_skips_inserts(self, store):
        """If the conversation already exists, neither INSERT runs."""
        s, db = store
        db.queue([{"conversation_id": "conv_existing"}])  # lookup hit
        conv_id = _run(
            s.get_or_create_conversation(
                "trv_meridian_demo",
                conversation_id="conv_existing",
                transaction_id="tx-3",
            )
        )
        assert conv_id == "conv_existing"
        # One SELECT only — no INSERTs.
        assert len(db.calls) == 1
        sql, params, _ = db.calls[0]
        assert "SELECT conversation_id FROM conversations" in sql
        assert params == ("conv_existing",)

    def test_missing_existing_conversation_falls_through_to_insert(self, store):
        """If a conversation_id is passed but doesn't exist, we still upsert traveler + create."""
        s, db = store
        db.queue([])  # lookup miss
        conv_id = _run(
            s.get_or_create_conversation(
                "trv_meridian_demo",
                conversation_id="conv_missing",
                transaction_id="tx-4",
            )
        )
        assert conv_id == "conv_missing"
        assert len(db.calls) == 3
        assert "SELECT conversation_id FROM conversations" in db.calls[0][0]
        assert "INSERT INTO travelers" in db.calls[1][0]
        assert "INSERT INTO conversations" in db.calls[2][0]


class TestVectorLiteral:
    """The Postgres vector literal helper is pure — quick property coverage."""

    def test_formats_floats_as_postgres_vector(self):
        assert MemoryStore._vector_literal([1.0, 2.5, -3.0]) == "[1.0,2.5,-3.0]"

    def test_empty_vector_produces_empty_brackets(self):
        assert MemoryStore._vector_literal([]) == "[]"


class TestRecallProfile:
    def test_includes_structured_loyalty_programs(self, store):
        s, db = store
        db.queue([{
            "full_name": "Alex Morgan",
            "loyalty_programs": {
                "marriott_bonvoy": {
                    "program": "Marriott Bonvoy",
                    "tier": "Platinum Elite",
                }
            },
        }])

        profile = _run(
            s.recall_profile("trv_meridian_demo", transaction_id="tx-profile")
        )

        assert profile["loyalty_programs"]["marriott_bonvoy"]["tier"] == "Platinum Elite"
        sql, params, tx = db.calls[0]
        assert "p.loyalty_programs" in sql
        assert params == ("trv_meridian_demo",)
        assert tx == "tx-profile"


class TestPreferenceMutation:
    def test_update_preference_is_scoped_and_upserts(self, store):
        s, db = store
        _run(s.update_preference("traveler-1", "home_airport", "JFK", transaction_id="tx-pref"))
        assert "INSERT INTO travelers" in db.calls[0][0]
        sql, params, tx = db.calls[1]
        assert "INSERT INTO traveler_preferences" in sql
        assert "ON CONFLICT" in sql
        assert params[1:] == ("traveler-1", "home_airport", "JFK")
        assert tx == "tx-pref"

    def test_delete_preference_binds_traveler_and_key(self, store):
        s, db = store
        _run(s.delete_preference("traveler-1", "home_airport", transaction_id="tx-pref"))
        sql, params, tx = db.calls[0]
        assert "DELETE FROM traveler_preferences" in sql
        assert params == ("traveler-1", "home_airport")
        assert tx == "tx-pref"
