"""Workflow state, span shape and stable identifiers for the Phase 5 graph."""

import hashlib
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TypedDict

AGENT_FILE = "agents/phase_05_workflow/graph.py"
HOLD_MINUTES = int(os.getenv("MERIDIAN_HOLD_MINUTES", "15"))
SNAPSHOT_STORE = "Aurora workflow_snapshots"


class WorkflowState(TypedDict, total=False):
    """The folded state the steps read. Run inputs plus every node's delta."""

    query: str
    traveler_id: str
    conversation_id: str
    journey_id: str
    travelers_count: int
    intent: str
    packages: List[Any]
    response: str
    activities: List[Dict[str, Any]]
    availability_checks: int
    hold_id: str
    hold_expires_at: str
    hold_created_at: str
    hold_observed_at: str
    hold_status: str
    hold_package: str
    hold_duration: str
    hold_seats_remaining: int
    hold_intent: Dict[str, Any]


class WorkflowAuthorizationError(PermissionError):
    """A caller tried to reach a workflow thread that is not theirs.

    Distinct from a generic failure so the API can answer 403 rather than 500:
    the request was understood and refused, not broken.
    """


def utc_now() -> datetime:
    """Now, in UTC."""
    return datetime.now(timezone.utc)


def utc_timestamp() -> str:
    """Now, as an ISO 8601 UTC string ending in Z."""
    return utc_now().isoformat().replace("+00:00", "Z")


def activity(
    activity_type: str,
    title: str,
    *,
    details: Optional[str] = None,
    agent_name: str = "OrchestrationAgent",
    telemetry: Optional[Dict[str, Any]] = None,
    sql_query: Optional[str] = None,
    execution_time_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """One trace span in the shape the showcase renders."""
    return {
        "id": str(uuid.uuid4()),
        "timestamp": utc_timestamp(),
        "activity_type": activity_type,
        "title": title,
        "details": details,
        "sql_query": sql_query,
        "execution_time_ms": execution_time_ms,
        "agent_name": agent_name,
        "agent_file": AGENT_FILE,
        "telemetry": telemetry,
    }


def coerce_activity(entry: Any) -> Dict[str, Any]:
    """Convert an ActivityEntry, pydantic model or dict into the span dict shape."""
    if isinstance(entry, dict):
        return entry
    if hasattr(entry, "model_dump"):
        return entry.model_dump()
    if hasattr(entry, "__dict__"):
        return dict(entry.__dict__)
    return {"title": str(entry)}


def hold_key(thread_id: str, package_id: str, duration: str) -> str:
    """A stable id for one intended hold.

    A graph step runs at least once, not exactly once, so the external effect
    carries its own identity. Deriving the key from the thread and what is held
    means a retry presents the same booking id, and the primary key on
    ``bookings`` rejects the duplicate instead of reserving inventory twice.
    """
    digest = hashlib.sha256(f"{thread_id}|{package_id}|{duration}".encode()).hexdigest()
    return f"hold_{digest[:24]}"
