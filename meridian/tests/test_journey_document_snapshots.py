"""The journey document's workflow section comes from the newest snapshot row."""

import json

import pytest

from backend.agents.phase_05_workflow.graph import snapshot_key
from backend.db import journey_document
from backend.db.journey_document import (
    SNAPSHOT_HISTORY_SQL,
    SNAPSHOT_SQL,
    SNAPSHOT_WRITER_SQL,
    _snapshot,
    _workflow_document,
    checkpoint_backend_is_durable,
)

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
        done, CHECKPOINT, executions, latest_execution="exe_2", resumed_from="5",
        resumed_from_writer="worker-a",
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
        ("AuroraDataApiSaver", False),
        ("PostgresSaver (Aurora \u00b7 pooled)", False),
        ("MemorySaver (in-process)", False),
        ("", False),
        ("SomethingElse", False),
    ],
)
def test_only_the_aurora_backed_kinds_are_durable(kind, durable):
    assert checkpoint_backend_is_durable(kind) is durable


async def test_the_workflow_is_read_by_its_own_storage_key_not_just_the_session():
    asked = []

    async def q(sql, params):
        asked.append((sql, params))
        if sql == SNAPSHOT_SQL:
            return [{"seq": "7", "snapshot": json.dumps(PAUSED), "saved_at": "2026-10-06 20:00:00",
                     "execution_id": "exe_1"}]
        return [{"n": 1, "resumed_from": None, "previous_seq": None}]

    await _snapshot(q, "t1")
    key = snapshot_key("t1")
    assert (SNAPSHOT_SQL, ("t1", key)) in asked
    assert (SNAPSHOT_HISTORY_SQL, ("exe_1", "7", "t1", key)) in asked


@pytest.mark.parametrize(("worker", "session", "vm"), [
    ("rt-wf-phase5-abc-0123/vm-0123456789ab", "rt-wf-phase5-abc-0123", "vm-0123456789ab"),
    ("worker-1a2b3c4d", None, None),
    ("a/b/c", None, None),
    (None, None, None),
])
def test_runtime_ids_come_from_the_worker_id(worker, session, vm):
    assert journey_document.runtime_ids(worker) == (session, vm)


def test_a_resume_after_a_stop_is_a_restart_when_the_snapshot_writer_differs():
    assert journey_document.restarted("rt-wf-x/vm-aaaaaaaaaaaa", "rt-wf-x/vm-bbbbbbbbbbbb") is True
    assert journey_document.restarted("rt-wf-x/vm-aaaaaaaaaaaa", "rt-wf-x/vm-aaaaaaaaaaaa") is False
    assert journey_document.restarted(None, "rt-wf-x/vm-bbbbbbbbbbbb") is False


def test_a_resume_by_the_same_worker_that_wrote_the_snapshot_is_not_a_restart():
    done = json.loads(json.dumps(PAUSED))
    done["data"]["state"].update(status="completed", next_nodes_to_execute=[])
    executions = {"status": "observed", "items": [
        {"execution_id": "exe_1", "worker_id": "worker-a", "status": "paused"},
        {"execution_id": "exe_2", "worker_id": "worker-a", "status": "succeeded"},
    ]}
    doc = _workflow_document(
        done, CHECKPOINT, executions, latest_execution="exe_2", resumed_from="5",
        resumed_from_writer="worker-a",
    )
    assert doc["workflow_status"] == "resumed"
    assert doc["resumed_after_restart"] is False


async def test_the_writer_of_the_resumed_from_snapshot_is_read_by_its_seq():
    asked = []

    async def q(sql, params):
        asked.append((sql, params))
        if sql == SNAPSHOT_SQL:
            return [{"seq": "9", "snapshot": json.dumps(PAUSED), "saved_at": "2026-10-06 20:00:00",
                     "execution_id": "exe_2"}]
        if sql == SNAPSHOT_WRITER_SQL:
            return [{"worker_id": "rt-wf-t/vm-aaaaaaaaaaaa"}]
        return [{"n": 2, "resumed_from": "5", "previous_seq": "5"}]

    result = await _snapshot(q, "t1")
    assert (SNAPSHOT_WRITER_SQL, ("5",)) in asked
    assert result[-1] == "rt-wf-t/vm-aaaaaaaaaaaa"
