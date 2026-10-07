"""The journey document's workflow section comes from the newest snapshot row."""

import json

import pytest

from backend.agents.phase_05_workflow.graph import snapshot_key
from backend.db.journey_document import (
    SNAPSHOT_HISTORY_SQL,
    SNAPSHOT_SQL,
    _snapshot,
    _workflow_document,
    checkpoint_backend_is_durable,
)
from backend.db.snapshot_text import BY_SEQ_SQL, WINDOW_CHARS

CLASSIFY_DELTA = json.dumps(
    {"state": {"intent": "plan"}, "spans": [{"title": "Workflow node: classify"}]}
)
PAUSED = {"data": {"state": {
    "status": "interrupted", "next_nodes_to_execute": ["availability"],
    "current_task": json.dumps({"query": "q", "conversation_id": "t1", "travelers_count": 3,
                                "traveler_id": "trv_x", "journey_id": "j"}),
    "execution_order": ["classify"],
    "node_results": {"classify": {"status": "completed", "result": {
        "type": "agent_result",
        "message": {"role": "assistant", "content": [{"text": CLASSIFY_DELTA}]},
    }}},
}}}
CHECKPOINT = {"status": "committed", "thread_id": "t1", "checkpoint_id": "7"}
EXECUTIONS = {"status": "observed", "items": [
    {"execution_id": "exe_1", "worker_id": "worker-a", "status": "paused"},
]}


def test_a_paused_snapshot_reads_as_paused_with_its_next_step():
    doc = _workflow_document(
        PAUSED, CHECKPOINT, EXECUTIONS, latest_execution="exe_1", resumed_from=None
    )
    assert doc["workflow_status"] == "paused"
    assert doc["next_nodes"] == ["availability"]
    assert doc["travelers_count"] == 3
    assert doc["conversation_id"] == "t1"
    assert doc["activities"] == [{"title": "Workflow node: classify"}]
    assert doc["source"] == "snapshot:t1/7#channel:workflow"


def test_a_completed_run_after_a_restart_reads_as_resumed():
    done = json.loads(json.dumps(PAUSED))
    done["data"]["state"].update(status="completed", next_nodes_to_execute=[])
    executions = {"status": "observed", "items": [
        {"execution_id": "exe_1", "worker_id": "worker-a", "status": "paused"},
        {"execution_id": "exe_2", "worker_id": "worker-b", "status": "succeeded"},
    ]}
    doc = _workflow_document(
        done, CHECKPOINT, executions, latest_execution="exe_2", resumed_from="5"
    )
    assert doc["workflow_status"] == "resumed"
    assert doc["resumed_from_checkpoint"] == "5"
    assert doc["resumed_after_restart"] is True
    assert doc["execution_id"] == "exe_2"
    assert doc["next_nodes"] == []


def test_two_executions_with_no_earlier_saved_step_is_not_a_resume():
    done = json.loads(json.dumps(PAUSED))
    done["data"]["state"].update(status="completed", next_nodes_to_execute=[])
    executions = {"status": "observed", "items": [
        {"execution_id": "exe_1", "worker_id": "worker-a", "status": "failed"},
        {"execution_id": "exe_2", "worker_id": "worker-b", "status": "succeeded"},
    ]}
    doc = _workflow_document(
        done, CHECKPOINT, executions, latest_execution="exe_2", resumed_from=None
    )
    assert doc["workflow_status"] == "complete"
    assert doc["resumed_from_checkpoint"] is None
    assert doc["resumed_after_restart"] is False


@pytest.mark.parametrize(
    ("kind", "durable"),
    [
        ("Aurora workflow_snapshots", True),
        ("AuroraDataApiSaver", True),
        ("PostgresSaver (Aurora \u00b7 pooled)", True),
        ("MemorySaver (in-process)", False),
        ("", False),
        ("SomethingElse", False),
    ],
)
def test_only_the_aurora_backed_kinds_are_durable(kind, durable):
    assert checkpoint_backend_is_durable(kind) is durable


def _windows(doc: str):
    return [{"part": i, "chunk": doc[i * WINDOW_CHARS:(i + 1) * WINDOW_CHARS]}
            for i in range((len(doc) - 1) // WINDOW_CHARS + 1)]


async def test_the_workflow_is_read_by_its_own_storage_key_not_just_the_session():
    asked = []

    async def q(sql, params):
        asked.append((sql, params))
        if sql == SNAPSHOT_SQL:
            return [{"seq": "7", "saved_at": "2026-10-06 20:00:00", "execution_id": "exe_1"}]
        if sql == BY_SEQ_SQL:
            return _windows(json.dumps(PAUSED))
        return [{"n": 1, "resumed_from": None, "previous_seq": None}]

    checkpoint, snapshot, _, _ = await _snapshot(q, "t1")
    key = snapshot_key("t1")
    assert (SNAPSHOT_SQL, ("t1", key)) in asked
    assert (BY_SEQ_SQL, ("7",)) in asked
    assert (SNAPSHOT_HISTORY_SQL, ("exe_1", "7", "t1", key)) in asked
    assert "snapshot::TEXT" not in SNAPSHOT_SQL
    assert checkpoint["status"] == "committed"
    assert snapshot == PAUSED


async def test_a_snapshot_that_disappears_between_the_two_reads_is_unavailable_evidence():
    async def q(sql, params):
        if sql == SNAPSHOT_SQL:
            return [{"seq": "7", "saved_at": "2026-10-06 20:00:00", "execution_id": "exe_1"}]
        return []

    checkpoint, snapshot, execution, resumed_from = await _snapshot(q, "t1")
    assert snapshot is None and execution is None and resumed_from is None
    assert checkpoint["status"] == "unavailable"
    assert "snapshot 7 disappeared while it was read" in checkpoint["reason"]
