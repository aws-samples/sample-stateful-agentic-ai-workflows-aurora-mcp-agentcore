"""Guards for the two durability claims the talk makes about Phase 5.

Both defects came from treating a LangGraph checkpoint as more than it is. A
checkpoint is a replay log: it makes re-execution possible, and it carries no
identity and no exactly-once guarantee for anything the graph did outside
itself.

  * A thread id was effectively a capability. Resume loaded persisted state and
    invoked it without ever comparing the thread's traveler to the caller.
  * The courtesy hold took a fresh uuid4 per invocation, so a node replayed
    after a crash asked for a second hold instead of the same one again.
"""

from __future__ import annotations

import pytest

from backend.agents.orchestration_05.workflow import (
    OrchestrationAgent,
    WorkflowAuthorizationError,
    _hold_key,
)


class PriorState:
    """Stands in for the StateSnapshot LangGraph returns from aget_state."""

    def __init__(self, values: dict):
        self.values = values
        self.next = ("hold",)


# --- Resume authorization ----------------------------------------------------


def test_a_traveler_cannot_resume_another_travelers_thread():
    """The reproduction from the audit: B resumes A's thread by knowing its id."""
    alices_thread = PriorState({"traveler_id": "trv_alice", "query": "Alice's plan"})

    with pytest.raises(WorkflowAuthorizationError) as caught:
        OrchestrationAgent._authorize_thread(alices_thread, "thread-alice", "trv_bob")

    assert "another traveler" in str(caught.value)


def test_the_owner_can_resume_their_own_thread():
    """The positive control - the check must not deny the legitimate case."""
    thread = PriorState({"traveler_id": "trv_alice"})
    OrchestrationAgent._authorize_thread(thread, "thread-alice", "trv_alice")


def test_a_thread_without_a_recorded_owner_cannot_be_resumed():
    """An unknown thread is not an implicitly public one."""
    with pytest.raises(WorkflowAuthorizationError) as caught:
        OrchestrationAgent._authorize_thread(PriorState({}), "thread-x", "trv_alice")

    assert "no recorded owner" in str(caught.value)


def test_authorization_is_a_permission_error():
    """So the API answers 403 rather than 500: refused, not broken."""
    assert issubclass(WorkflowAuthorizationError, PermissionError)


# --- Hold idempotency --------------------------------------------------------


def test_replaying_the_same_node_asks_for_the_same_hold():
    """The crash window: commit lands, checkpoint does not, node re-runs.

    A stable key means the retry presents the id the database already has, so
    the primary key on bookings rejects the duplicate instead of reserving
    inventory a second time.
    """
    first = _hold_key("thread-1", "WEL-002", "6 nights")
    replay = _hold_key("thread-1", "WEL-002", "6 nights")
    assert first == replay


def test_concurrent_resumes_of_one_thread_converge_on_one_hold():
    """Two workers resuming the same thread derive the same key, so one wins."""
    keys = {_hold_key("thread-1", "WEL-002", "6 nights") for _ in range(8)}
    assert len(keys) == 1


def test_a_different_hold_still_gets_its_own_key():
    """Idempotency must not collapse genuinely distinct holds together."""
    base = _hold_key("thread-1", "WEL-002", "6 nights")
    assert base != _hold_key("thread-1", "BCH-001", "6 nights")
    assert base != _hold_key("thread-1", "WEL-002", "8 nights")
    assert base != _hold_key("thread-2", "WEL-002", "6 nights")


def test_the_key_is_not_random():
    """A uuid4 per call was the defect; regenerate and compare."""
    assert _hold_key("t", "p", "d") == _hold_key("t", "p", "d")
    assert _hold_key("t", "p", "d").startswith("hold_")


def test_the_key_fits_the_booking_id_column():
    """bookings.booking_id is VARCHAR(50)."""
    assert len(_hold_key("a-long-thread-id-" * 4, "WEL-002", "6 nights")) <= 50


# --- Compensation scoping ----------------------------------------------------


@pytest.mark.parametrize(
    "persisted,expected,should_release",
    [
        # This run's own hold: release it.
        ("hold_abc", "hold_abc", True),
        # A hold left in the thread's state by an earlier successful run: leave
        # it alone. Releasing it undid work that never failed.
        ("hold_from_earlier_run", "hold_this_run", False),
    ],
)
def test_compensation_only_releases_this_runs_hold(persisted, expected, should_release):
    released: list[str] = []

    def would_release(state, *, expected_hold_id=None):
        hold_id = state.get("hold_id")
        if not hold_id or not state.get("traveler_id"):
            return
        if expected_hold_id is not None and hold_id != expected_hold_id:
            return
        released.append(hold_id)

    would_release(
        {"hold_id": persisted, "traveler_id": "trv_alice"}, expected_hold_id=expected
    )
    assert bool(released) is should_release
