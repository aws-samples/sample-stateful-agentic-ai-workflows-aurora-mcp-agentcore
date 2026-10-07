"""Cross-traveler counts for the presenter evidence endpoints.

The backend login cannot read other travelers' rows, and it must not: row-level security is the
boundary the presenter proofs demonstrate. Two endpoints still report totals across every
traveler, and the master login used to supply them by ignoring RLS. Migration 018 gives the
backend login one definer function, ``backend_admin_count``, that answers only the kinds below
and returns a count and nothing else.
"""

from typing import Any, Optional

RLS_BASELINE_KINDS = {
    "traveler_preferences": "rls_traveler_preferences",
    "trip_interactions": "rls_trip_interactions",
    "conversations": "rls_conversations",
    "conversation_messages": "rls_conversation_messages",
}


async def admin_count(
    db: Any, kind: str, *, window: Optional[str] = None, key: Optional[str] = None
) -> int:
    """Return one cross-traveler count from ``backend_admin_count``.

    Args:
        db: The Data API client, outside any traveler scope.
        kind: One of the kinds migration 018 defines, such as ``audit_deny``.
        window: A Postgres interval such as ``90 minutes``, required by the audit kinds.
        key: The workflow session id, required by ``workflow_snapshots``.

    Returns:
        The count, or 0 when Aurora answers with no row.

    Raises:
        Exception: Whatever the Data API raises, including Aurora's refusal of an unknown
            kind or of a kind called without its window or key.
    """
    rows = await db.execute(
        "SELECT backend_admin_count(%s, %s::interval, %s) AS n", (kind, window, key)
    )
    return int(rows[0]["n"]) if rows and rows[0]["n"] is not None else 0
