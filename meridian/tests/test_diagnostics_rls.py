"""Unit tests for the RLS-probe diagnostics endpoint's safety guard.

The endpoint interpolates table names into SQL (names can't be bound params),
so the allow-list is the injection boundary. These tests assert that boundary
holds without needing a live database.
"""

from backend.routers.diagnostics import ALLOWED_TABLES, DEFAULT_TABLES


def _filter_tables(requested):
    """Mirror the filter the endpoint applies to request.tables."""
    return [t for t in requested if t in ALLOWED_TABLES] or list(DEFAULT_TABLES)


def test_unknown_table_is_dropped():
    result = _filter_tables(["traveler_preferences", "pg_shadow", "users"])
    assert result == ["traveler_preferences"]


def test_injection_attempt_is_dropped():
    result = _filter_tables(["traveler_preferences", "x; DROP TABLE y"])
    assert result == ["traveler_preferences"]
    assert all(t in ALLOWED_TABLES for t in result)


def test_all_invalid_falls_back_to_defaults():
    assert _filter_tables(["evil", "nope"]) == list(DEFAULT_TABLES)


def test_valid_subset_is_preserved():
    assert _filter_tables(["conversations"]) == ["conversations"]


def test_allowlist_only_contains_rls_tables():
    # Guard against someone widening the list to a sensitive catalog table.
    assert set(ALLOWED_TABLES) == {
        "traveler_preferences",
        "trip_interactions",
        "conversations",
        "conversation_messages",
    }
