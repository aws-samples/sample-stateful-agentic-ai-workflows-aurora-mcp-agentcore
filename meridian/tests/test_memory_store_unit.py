"""Unit tests for ``backend.memory.store``.

These tests exercise the SQL the MemoryStore emits without touching Aurora —
they record every call to the DB client and assert on shape + ordering.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

from backend.audit_vocabulary import ToolCall
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


def test_shortlist_recall_stays_inside_the_conversation_and_rls_transaction(store):
    s, db = store
    packages = [{"package_id": "TKY-001", "name": "Tokyo Indie Walk"}]
    db.queue([{"packages_shown": packages}])
    assert _run(s.recall_shown_packages("conv-existing", transaction_id="tx-scoped")) == packages
    sql, params, tx = db.calls[0]
    assert "WHERE conversation_id = %s" in sql
    assert params == ("conv-existing",)
    assert tx == "tx-scoped"


def test_boolean_memory_keeps_its_meaning_in_model_context(store):
    s, _ = store
    context = s.format_memory_context(None, [], [{"key": "no_red_eye", "value": "true", "confidence": 1}], [])
    assert "no_red_eye: true" in context


class TestRecallProfile:
    def test_includes_structured_loyalty_programs(self, store):
        s, db = store
        db.queue([{
            "full_name": "Jordan Morgan",
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


class TestWriteAudit:
    AUDIT = dict(
        agent_name="ProductionAgent", operation="production_hold", traveler_id="trv_1",
        rls_traveler="trv_1", rls_agent_type="concierge_agent",
        iam_identity="arn:aws:iam::123456789012:role/x", authorization_provider="cognito",
        authorization_subject="sub-1", authorization_decision="deny", rows_returned=1,
        transaction_id="tx",
    )
    CALLS = [
        ToolCall("get_package_details", "allowed"),
        ToolCall("create_courtesy_hold", "refused", "policy"),
    ]

    def test_records_every_call_in_order_as_one_json_array(self, store):
        s, db = store
        audit_id = _run(s.write_audit(**self.AUDIT, tool_calls=self.CALLS))

        sql, params, tx = db.calls[0]
        assert "rows_returned, tool_calls, turn_error" in " ".join(sql.split())
        assert "%s::jsonb, %s)" in sql
        assert params[4:7] == ("cognito", "sub-1", "deny")
        assert params[-1] is None
        assert json.loads(params[-2]) == [
            {"tool": "get_package_details", "result": "allowed", "refused_by": None,
             "refusal_code": None, "access_audit_id": None},
            {"tool": "create_courtesy_hold", "result": "refused", "refused_by": "policy",
             "refusal_code": None, "access_audit_id": None},
        ]
        assert tx == "tx" and audit_id.startswith("aud_")

    def test_unknown_calls_are_null_and_no_calls_are_an_empty_array(self, store):
        s, db = store
        _run(s.write_audit(**self.AUDIT))
        _run(s.write_audit(**{**self.AUDIT, "authorization_decision": "allow"}, tool_calls=[]))
        assert db.calls[0][1][-2] is None
        assert db.calls[1][1][-2] == "[]"

    FAILED = "ClientError: An error occurred (AccessDeniedException)"

    @pytest.mark.parametrize(("decision", "calls"), [
        ("error", [ToolCall("get_package_details", "allowed")]),
        ("error", []),
        ("deny", CALLS),
        ("error", None),
        ("deny", None),
    ])
    def test_a_failed_turn_stores_its_error_beside_its_calls(self, store, decision, calls):
        s, db = store
        _run(s.write_audit(**{**self.AUDIT, "authorization_decision": decision},
                           tool_calls=calls, turn_error=self.FAILED))
        assert db.calls[0][1][-1] == self.FAILED

    @pytest.mark.parametrize(("turn_error", "problem"), [
        ("", "turn_error must be None or a non-empty text"),
        ("x" * 401, "turn_error must be None or a non-empty text"),
        (7, "turn_error must be None or a non-empty text"),
    ])
    def test_a_turn_error_outside_the_checks_is_refused_before_any_sql(
            self, store, turn_error, problem):
        s, db = store
        with pytest.raises(ValueError, match=problem):
            _run(s.write_audit(**{**self.AUDIT, "authorization_decision": "error"},
                               turn_error=turn_error))
        assert db.calls == []

    @pytest.mark.parametrize(("decision", "calls", "problem"), [
        ("allow", None, "turn_error cannot have authorization_decision 'allow'"),
        ("allow", [ToolCall("get_package_details", "allowed")],
         "turn_error cannot have authorization_decision 'allow'"),
        ("error", CALLS, "authorization_decision must be 'deny'"),
        ("deny", [ToolCall("get_package_details", "allowed")],
         "authorization_decision must be 'error'"),
    ])
    def test_a_failed_turn_whose_decision_breaks_the_rule_is_refused(
            self, store, decision, calls, problem):
        s, db = store
        with pytest.raises(ValueError, match=problem):
            _run(s.write_audit(**{**self.AUDIT, "authorization_decision": decision},
                               tool_calls=calls, turn_error=self.FAILED))
        assert db.calls == []

    @pytest.mark.parametrize(("call", "problem"), [
        (ToolCall("create_courtesy_hold", "maybe"), "result"),
        (ToolCall("create_courtesy_hold", "refused", "cedar"), "refused_by"),
        (ToolCall("create_courtesy_hold", "refused"), "refused_by"),
        (ToolCall("create_courtesy_hold", "allowed", "policy"), "refused_by"),
        (ToolCall("create_courtesy_hold", "allowed", ""), "refused_by"),
        ({"tool": "create_courtesy_hold", "result": "allowed"}, "ToolCall"),
        # The rest mirror the migration 021 CHECKs on tool, refusal_code and access_audit_id.
        (ToolCall("", "allowed"), "tool"),
        (ToolCall(5, "allowed"), "tool"),
        (ToolCall("create_courtesy_hold", "error", refusal_code=7), "refusal_code"),
        (ToolCall("create_courtesy_hold", "allowed", access_audit_id=7), "access_audit_id"),
        (ToolCall("create_courtesy_hold", "refused", "workload_grant", "traveler_not_authorized"),
         "access_audit_id"),
        (ToolCall("create_courtesy_hold", "error", refusal_code=""), "refusal_code"),
        (ToolCall("create_courtesy_hold", "refused", "policy", access_audit_id="authz_1"),
         "access_audit_id"),
    ])
    def test_a_call_outside_the_vocabulary_is_refused_before_any_sql(self, store, call, problem):
        s, db = store
        with pytest.raises(ValueError, match=problem):
            _run(s.write_audit(**self.AUDIT, tool_calls=[call]))
        assert db.calls == []

    @pytest.mark.parametrize(("decision", "calls"), [
        ("allow", CALLS),
        ("deny", [ToolCall("create_courtesy_hold", "refused", "business_rule",
                           "insufficient_inventory")]),
        ("allow", [ToolCall("get_package_details", "error")]),
        (None, []),
    ])
    def test_a_decision_the_calls_do_not_support_is_refused(self, store, decision, calls):
        s, db = store
        with pytest.raises(ValueError, match="authorization_decision"):
            _run(s.write_audit(**{**self.AUDIT, "authorization_decision": decision},
                               tool_calls=calls))
        assert db.calls == []
