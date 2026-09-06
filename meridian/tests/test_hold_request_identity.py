"""The expanded hold function must be the only callable path.

CREATE OR REPLACE with a changed parameter list creates an overload rather
than replacing, so leaving the old eight-argument function in place would
leave a path that bypasses idempotency entirely.
"""

from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).parent.parent
MIGRATION = ROOT / "scripts" / "migrations" / "008_hold_request_identity.sql"
BOOTSTRAP = ROOT / "examples" / "rls_for_agents.sql"

NEW_SIGNATURE = "text, text, text, text, text, text, text, integer, numeric, numeric, timestamptz"
OLD_SIGNATURE = "text, text, text, text, integer, numeric, numeric, timestamptz"


@pytest.fixture
def sql() -> str:
    return MIGRATION.read_text()


@pytest.fixture
def normalized(sql: str) -> str:
    return " ".join(sql.split()).lower()


@pytest.fixture
def bootstrap() -> str:
    return " ".join(BOOTSTRAP.read_text().split()).lower()


# ------------------------------------------------------- replacing the old


def test_drops_the_old_signature(normalized: str) -> None:
    assert "drop function if exists create_courtesy_hold(" in normalized
    assert OLD_SIGNATURE in normalized


def test_revokes_the_old_grant(normalized: str) -> None:
    assert "revoke" in normalized


def test_new_signature_takes_journey_and_request(normalized: str) -> None:
    assert "p_journey_id" in normalized
    assert "p_hold_request_id" in normalized
    assert "p_fingerprint" in normalized


# --------------------------------------------------------- authorization


def test_authorizes_the_journey_owner(normalized: str) -> None:
    assert "from journeys" in normalized
    assert "journey_not_owned" in normalized


def test_authorization_precedes_the_conflict_branch(normalized: str) -> None:
    owner_at = normalized.index("journey_not_owned")
    conflict_at = normalized.index("on conflict")
    assert owner_at < conflict_at, "replay must be authorized too"


def test_keeps_the_existing_scope_and_agent_checks(normalized: str) -> None:
    assert "traveler_scope_mismatch" in normalized
    assert "booking_agent_not_authorized" in normalized


# ---------------------------------------------------------- idempotency


def test_inserts_identity_before_taking_the_lock(normalized: str) -> None:
    insert_at = normalized.index("insert into hold_requests")
    lock_at = normalized.index("pg_advisory_xact_lock")
    assert insert_at < lock_at


def test_replay_returns_without_touching_inventory(normalized: str) -> None:
    """A replay must return before the lock, not after re-checking inventory."""
    assert "hold_request_parameter_mismatch" in normalized
    replay = normalized[
        normalized.index("if not v_inserted") : normalized.index(
            "pg_advisory_xact_lock"
        )
    ]
    assert "insert into bookings" not in replay
    assert "return;" in replay, "the replay branch must return, not fall through"


def test_returns_a_replay_flag(normalized: str) -> None:
    assert "replayed" in normalized


def test_keeps_the_inventory_guard(normalized: str) -> None:
    assert "insufficient_inventory" in normalized


# ------------------------------------------------------------------ grants


def test_grants_execute_to_the_app_role_only(normalized: str) -> None:
    assert "grant execute on function create_courtesy_hold" in normalized
    assert "to meridian_app" in normalized
    assert "to public" not in normalized


# ------------------------------------------------------------- legacy rows


def test_legacy_backfill_only_links_unambiguous_holds(normalized: str) -> None:
    assert "count(*) from journeys j2" in normalized
    assert "count(*) from bookings b2" in normalized


def test_legacy_rows_cannot_be_replayed_as_a_match(normalized: str) -> None:
    assert "legacy:unverified" in normalized


# --------------------------------------------------------------- bootstrap


def test_a_fresh_bootstrap_does_not_recreate_the_old_signature(
    bootstrap: str,
) -> None:
    """A fresh database must not get the dropped overload back.

    `init_aurora_schema.py` runs examples/rls_for_agents.sql. If that file
    still creates the eight-argument function, a bootstrapped database ends up
    with both, and which one a call binds to depends on argument types.
    """
    assert "create or replace function create_courtesy_hold(" in bootstrap
    start = bootstrap.index("create or replace function create_courtesy_hold(")
    signature = bootstrap[start : start + 600]
    assert "p_journey_id" in signature
    assert "p_hold_request_id" in signature


def test_the_bootstrap_grants_the_new_signature(bootstrap: str) -> None:
    assert NEW_SIGNATURE in bootstrap
    assert f"grant execute on function create_courtesy_hold( {NEW_SIGNATURE} ) to meridian_app" in bootstrap
