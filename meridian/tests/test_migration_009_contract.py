"""The audit table must be readable by the app, and only for its own traveler.

Presenter proof sources authorization from this table, so the application role
needs SELECT. Granting it without a policy would let any scoped session read
every traveler's authorization history from a table whose entire purpose is
proving who was allowed to do what.
"""

from __future__ import annotations

import pathlib

import pytest

MIGRATION = (
    pathlib.Path(__file__).parent.parent
    / "scripts" / "migrations" / "009_scope_traveler_access_audit.sql"
)


@pytest.fixture
def sql() -> str:
    return " ".join(MIGRATION.read_text().split()).lower()


def test_row_level_security_is_enabled(sql: str) -> None:
    assert "alter table traveler_access_audit enable row level security" in sql


def test_the_policy_scopes_to_the_requesting_traveler(sql: str) -> None:
    assert "requested_traveler_id = current_setting('app.current_traveler_id', true)" in sql


def test_the_policy_is_created_before_the_grant(sql: str) -> None:
    """A grant landing first would open a window with no policy behind it."""
    assert sql.index("create policy") < sql.index("grant select")


def test_only_select_is_granted(sql: str) -> None:
    assert "grant select on traveler_access_audit to meridian_app" in sql
    assert "insert" not in sql.split("grant")[1]
    assert "to public" not in sql
