"""The one rule for a finished Phase 5 run: its ``synthesize`` step completed.

The run outcome, the resume guard and the stop route all read it here, so they cannot
disagree. A snapshot's ``status`` field is not the rule: the invocation-end save that
sets ``completed`` can fail after ``synthesize`` finished, and that run is still finished.
"""

from typing import Any, Dict, Iterable, Optional

from backend.agents.phase_05_workflow.graph import LAST_NODE


def run_finished(completed_nodes: Iterable[str]) -> bool:
    """Whether a run whose completed node ids are ``completed_nodes`` is finished."""
    return LAST_NODE in set(completed_nodes)


def snapshot_finished(snapshot: Optional[Dict[str, Any]]) -> bool:
    """Whether a persisted snapshot records a finished run; None and malformed ones do not."""
    if not isinstance(snapshot, dict):
        return False
    state = (snapshot.get("data") or {}).get("state") or {}
    return run_finished(state.get("completed_nodes") or [])
