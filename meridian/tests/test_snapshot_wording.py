"""User-visible Phase 5 text says snapshot or saved step, never checkpoint."""

import json

import pytest

from backend.db.journey_document import (
    _hold_absent,
    _pending_decision,
    _recommendations,
    _selected_plan,
    _workflow_document,
)
from tests.test_journey_document_snapshots import SAVED_SNAPSHOT, EXECUTIONS, PAUSED

UNCOMMITTED = {"status": "pending", "thread_id": "t1", "snapshot_id": "7"}


def _no_checkpoint(text: str) -> None:
    assert "checkpoint" not in text.lower(), text


def test_a_paused_journey_message_says_saved_step():
    doc = _workflow_document(
        PAUSED, SAVED_SNAPSHOT, EXECUTIONS, latest_execution="exe_1", resumed_from=None
    )
    _no_checkpoint(doc["message"])
    assert "saved step" in doc["message"]


@pytest.mark.parametrize(
    "reason",
    [
        _selected_plan(UNCOMMITTED, None)["reason"],
        _selected_plan(SAVED_SNAPSHOT, None)["reason"],
        _recommendations(UNCOMMITTED, None)["reason"],
        _recommendations(SAVED_SNAPSHOT, None)["reason"],
        _pending_decision(UNCOMMITTED, None)["reason"],
        _pending_decision(SAVED_SNAPSHOT, None)["reason"],
        _hold_absent({"hold_id": "HLD-1"})["reason"],
    ],
)
def test_unavailable_evidence_reasons_say_snapshot(reason):
    _no_checkpoint(reason)
    assert "snapshot" in reason


def test_the_empty_channel_reasons_are_json_text_the_ui_can_show():
    reasons = [_selected_plan(SAVED_SNAPSHOT, None), _recommendations(SAVED_SNAPSHOT, None)]
    _no_checkpoint(json.dumps(reasons))
