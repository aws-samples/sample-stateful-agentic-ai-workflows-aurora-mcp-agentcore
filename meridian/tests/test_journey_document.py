"""GET /journeys/{journey_id} assembles evidence from what Aurora actually holds.

The document is what Presenter proof renders. Its whole value is that every
claim is traceable: each section names the source it came from, the ids it
describes, and when it was observed, and evidence that does not exist says so
rather than being quietly omitted or inferred from prose.

Runs against the live cluster. Requires migrations 007 and 008.
"""

from __future__ import annotations

import json
import uuid
from typing import AsyncIterator

import pytest
import pytest_asyncio

from backend.agentcore.identity import get_agentcore_identity
from backend.db.journey_document import assemble_journey_document
from backend.db.journey_store import ScopedDb, bind_thread, claim_execution, create_journey
from backend.db.rds_data_client import get_rds_data_client

pytestmark = pytest.mark.database

TRAVELER = "trv_meridian_demo"
OTHER_TRAVELER = "trv_demo_decoy"


class Fixture:
    def __init__(self, client) -> None:
        self.client = client
        self.journey_id = ""
        self.thread_id = f"jdoc-{uuid.uuid4().hex[:10]}"
        self.booking_id = f"BKG-JDOC{uuid.uuid4().hex[:6].upper()}"

    def scoped(self):
        return self.client.scoped_session(
            traveler_id=TRAVELER,
            agent_type="booking_agent",
            authorization=get_agentcore_identity().authorization_context(),
        )

    async def purge(self) -> None:
        await self.client.execute(
            "DELETE FROM workflow_snapshots WHERE session_id = %s", (self.thread_id,)
        )
        await self.client.execute(
            "DELETE FROM hold_requests WHERE booking_id = %s", (self.booking_id,)
        )
        await self.client.execute(
            "DELETE FROM booking_lines WHERE booking_id = %s", (self.booking_id,)
        )
        await self.client.execute(
            "DELETE FROM bookings WHERE booking_id = %s", (self.booking_id,)
        )
        await self.client.execute(
            "DELETE FROM journey_executions WHERE thread_id = %s", (self.thread_id,)
        )
        await self.client.execute(
            "UPDATE journeys SET active_thread_id = NULL WHERE journey_id = %s",
            (self.journey_id,),
        )
        await self.client.execute(
            "DELETE FROM journey_threads WHERE thread_id = %s", (self.thread_id,)
        )
        await self.client.execute(
            "DELETE FROM hold_requests WHERE journey_id = %s", (self.journey_id,)
        )
        await self.client.execute(
            "DELETE FROM journeys WHERE journey_id = %s", (self.journey_id,)
        )


@pytest_asyncio.fixture
async def journey() -> AsyncIterator[Fixture]:
    fx = Fixture(get_rds_data_client())
    async with fx.scoped() as tx:
        db = ScopedDb(fx.client, tx)
        fx.journey_id = await create_journey(db, TRAVELER, "Aurora workflow_snapshots")
        await bind_thread(db, fx.journey_id, fx.thread_id)
    try:
        yield fx
    finally:
        await fx.purge()


async def _document(fx: Fixture, traveler_id: str = TRAVELER) -> dict:
    return await assemble_journey_document(fx.client, fx.journey_id, traveler_id)


# ------------------------------------------------------------------ identity


async def test_the_document_names_the_journey_its_owner_and_backend(
    journey: Fixture,
) -> None:
    doc = await _document(journey)
    assert doc["journey_id"] == journey.journey_id
    assert doc["traveler_id"] == TRAVELER
    assert doc["active_thread_id"] == journey.thread_id
    assert doc["checkpoint_backend"]["kind"] == "Aurora workflow_snapshots"
    assert doc["checkpoint_backend"]["durable"] is True


async def test_a_journey_the_caller_does_not_own_is_not_readable(
    journey: Fixture,
) -> None:
    """A journey id grants nothing on its own."""
    with pytest.raises(PermissionError):
        await _document(journey, traveler_id=OTHER_TRAVELER)


async def test_an_unknown_journey_is_not_found(journey: Fixture) -> None:
    with pytest.raises(LookupError):
        await assemble_journey_document(journey.client, "jrn_does_not_exist", TRAVELER)


# ------------------------------------------------- evidence that is not there


async def test_missing_evidence_says_so_rather_than_being_omitted(
    journey: Fixture,
) -> None:
    """A fresh journey has no checkpoint and no hold. Both must be explicit."""
    doc = await _document(journey)
    for section in ("checkpoint", "hold", "recommendations", "selected_plan"):
        assert section in doc, f"{section} must never be omitted"
        assert doc[section]["status"] == "unavailable"
        assert doc[section]["reason"], f"{section} must say why it is unavailable"


async def test_every_present_section_carries_its_source(journey: Fixture) -> None:
    doc = await _document(journey)
    for name, section in doc.items():
        if isinstance(section, dict) and section.get("status") != "unavailable":
            if name in ("checkpoint_backend",):
                continue
            assert section.get("source"), f"{name} must name its source"


# ---------------------------------------------------------------- executions


async def test_executions_report_workers_attempts_and_status(
    journey: Fixture,
) -> None:
    first = await claim_execution(
        journey.client, journey.journey_id, journey.thread_id, "worker_01"
    )
    from backend.db.journey_store import release_execution

    await release_execution(journey.client, first.execution_id, "abandoned")
    second = await claim_execution(
        journey.client, journey.journey_id, journey.thread_id, "worker_02"
    )

    doc = await _document(journey)
    executions = doc["executions"]["items"]
    assert doc["executions"]["source"] == "journey_executions"
    assert [e["attempt"] for e in executions] == [1, 2]
    assert [e["worker_id"] for e in executions] == ["worker_01", "worker_02"]
    assert executions[0]["status"] == "abandoned"
    assert executions[1]["status"] == "running"
    assert executions[1]["execution_id"] == second.execution_id


# ---------------------------------------------------------------- checkpoint


def _workflow_snapshot(fx: Fixture, status: str, next_nodes: list) -> dict:
    from backend.agents.phase_05_workflow.graph import run_task

    delta = {"state": {"hold_package": "TKY-003"}, "spans": []}
    task = run_task(
        query="q", traveler_id=TRAVELER, conversation_id=fx.thread_id,
        journey_id=fx.journey_id, travelers_count=1,
    )
    return {
        "scope": "multiAgent", "schema_version": "1.0", "app_data": {},
        "created_at": "2026-10-06T02:13:41+00:00",
        "data": {"orchestrator_id": "phase5", "state": {
            "type": "graph", "id": "phase5", "status": status,
            "completed_nodes": ["hold"], "failed_nodes": [], "interrupted_nodes": next_nodes,
            "next_nodes_to_execute": next_nodes, "execution_order": ["hold"],
            "current_task": task,
            "node_results": {"hold": {"status": "completed", "result": {
                "type": "agent_result", "stop_reason": "end_turn",
                "message": {"role": "assistant", "content": [{"text": json.dumps(delta)}]},
            }}},
        }},
    }


def _storage(fx: Fixture, execution_id: str, worker_id: str):
    from backend.agents.phase_05_workflow.snapshot_storage import AuroraSnapshotStorage

    return AuroraSnapshotStorage(
        fx.client, session_id=fx.thread_id, traveler_id=TRAVELER,
        execution_id=execution_id, worker_id=worker_id,
    )


async def test_a_saved_snapshot_is_reported_with_its_thread(journey: Fixture) -> None:
    from backend.agents.phase_05_workflow.graph import snapshot_key

    snapshot = _workflow_snapshot(journey, "interrupted", ["synthesize"])
    claim = await claim_execution(
        journey.client, journey.journey_id, journey.thread_id, "worker-jdoc"
    )
    storage = _storage(journey, claim.execution_id, "worker-jdoc")
    for _ in range(2):
        await storage.write(snapshot_key(journey.thread_id), json.dumps(snapshot).encode())

    doc = await _document(journey)
    checkpoint = doc["checkpoint"]
    assert checkpoint["status"] == "committed"
    assert checkpoint["source"] == "workflow_snapshots"
    assert checkpoint["thread_id"] == journey.thread_id
    assert checkpoint["snapshot_count"] == 2
    assert int(checkpoint["parent_checkpoint_id"]) < int(checkpoint["checkpoint_id"])
    assert doc["workflow"]["workflow_status"] == "paused"
    assert doc["workflow"]["next_nodes"] == ["synthesize"]
    plan = doc["selected_plan"]
    assert plan["package_id"] == "TKY-003"
    expected = f"snapshot:{journey.thread_id}/{checkpoint['checkpoint_id']}#channel:hold_package"
    assert plan["source"] == expected


async def test_another_key_in_the_same_session_is_not_read_as_the_workflow(
    journey: Fixture,
) -> None:
    from backend.agents.phase_05_workflow.graph import snapshot_key

    claim = await claim_execution(
        journey.client, journey.journey_id, journey.thread_id, "worker-jdoc"
    )
    workflow = _workflow_snapshot(journey, "interrupted", ["synthesize"])
    await _storage(journey, claim.execution_id, "worker-jdoc").write(
        snapshot_key(journey.thread_id), json.dumps(workflow).encode()
    )
    other_key = f"session/{journey.thread_id}/agents/helper/snapshots/snapshot_latest.json"
    stray = {"data": {"state": {"status": "completed", "next_nodes_to_execute": []}}}
    await _storage(journey, claim.execution_id, "worker-jdoc").write(
        other_key, json.dumps(stray).encode()
    )
    newest = await journey.client.execute(
        "SELECT storage_key FROM workflow_snapshots WHERE session_id = %s "
        "ORDER BY snapshot_seq DESC LIMIT 1", (journey.thread_id,),
    )
    assert newest[0]["storage_key"] == other_key, "the stray row must be the newest in the session"

    doc = await _document(journey)
    assert doc["workflow"]["workflow_status"] == "paused"
    assert doc["workflow"]["next_nodes"] == ["synthesize"]
    assert doc["checkpoint"]["snapshot_count"] == 1


async def test_a_run_finished_from_a_saved_step_is_a_verified_resume(journey: Fixture) -> None:
    """Two executions, the first saved a step, the second finished: what the UI verifies."""
    from backend.agents.phase_05_workflow.graph import snapshot_key
    from backend.db.journey_store import release_execution

    key = snapshot_key(journey.thread_id)
    first = await claim_execution(
        journey.client, journey.journey_id, journey.thread_id, "rt-wf-t/vm-aaaaaaaaaaaa"
    )
    paused = _workflow_snapshot(journey, "interrupted", ["synthesize"])
    await _storage(journey, first.execution_id, "rt-wf-t/vm-aaaaaaaaaaaa").write(
        key, json.dumps(paused).encode()
    )
    await release_execution(journey.client, first.execution_id, "paused")
    saved = await journey.client.execute(
        "SELECT snapshot_seq::TEXT AS seq FROM workflow_snapshots WHERE session_id = %s",
        (journey.thread_id,),
    )
    first_seq = saved[0]["seq"]

    second = await claim_execution(
        journey.client, journey.journey_id, journey.thread_id, "rt-wf-t/vm-bbbbbbbbbbbb"
    )
    finished = _workflow_snapshot(journey, "completed", [])
    await _storage(journey, second.execution_id, "rt-wf-t/vm-bbbbbbbbbbbb").write(
        key, json.dumps(finished).encode()
    )
    await release_execution(journey.client, second.execution_id, "succeeded")

    doc = await _document(journey)
    workflow = doc["workflow"]
    assert workflow["workflow_status"] == "resumed"
    assert workflow["resumed_from_checkpoint"] == first_seq
    assert workflow["execution_id"] == second.execution_id
    assert workflow["resumed_after_restart"] is True
    assert workflow["next_nodes"] == []
    assert workflow["conversation_id"] == journey.thread_id
    last = doc["executions"]["items"][-1]
    assert last["status"] == "succeeded"
    assert last["execution_id"] == second.execution_id
    assert last["microvm_id"] == "vm-bbbbbbbbbbbb"
    assert last["runtime_session_id"] == "rt-wf-t"


async def test_a_snapshot_larger_than_64_kb_is_read_for_the_document(
    journey: Fixture,
) -> None:
    from backend.agents.phase_05_workflow.graph import snapshot_key

    snapshot = _workflow_snapshot(journey, "interrupted", ["synthesize"])
    snapshot["data"]["state"]["padding"] = "é" * 120_000
    claim = await claim_execution(
        journey.client, journey.journey_id, journey.thread_id, "worker-jdoc"
    )
    await _storage(journey, claim.execution_id, "worker-jdoc").write(
        snapshot_key(journey.thread_id), json.dumps(snapshot).encode()
    )

    doc = await _document(journey)
    assert doc["checkpoint"]["status"] == "committed"
    assert doc["checkpoint"]["snapshot_count"] >= 1
    assert doc["workflow"]["workflow_status"] == "paused"
    assert doc["workflow"]["next_nodes"] == ["synthesize"]


# --------------------------------------------------------------------- hold


async def test_the_hold_is_reported_as_one_hold_for_this_request(
    journey: Fixture,
) -> None:
    from backend.agents.phase_05_workflow.hold_intent import (
        fingerprint_terms,
        normalize_hold_terms,
    )
    from decimal import Decimal

    request_id = f"hrq_{uuid.uuid4().hex[:12]}"
    fingerprint = fingerprint_terms(
        normalize_hold_terms("TKY-003", "3 nights", 1, Decimal("1949.00"))
    )
    async with journey.scoped() as tx:
        await journey.client.execute(
            """
            SELECT booking_id FROM create_courtesy_hold(
                %s::TEXT, %s::TEXT, %s::TEXT, %s::TEXT, %s::TEXT,
                %s::TEXT, %s::TEXT, %s::INTEGER, %s::NUMERIC, %s::NUMERIC,
                CURRENT_TIMESTAMP + interval '12 hours')
            """,
            (
                journey.booking_id,
                TRAVELER,
                journey.journey_id,
                request_id,
                fingerprint,
                "TKY-003",
                "3 nights",
                1,
                1949.00,
                1949.00,
            ),
            transaction_id=tx,
        )

    doc = await _document(journey)
    hold = doc["hold"]
    assert hold["status"] == "held"
    assert hold["label"] == "one hold for this request"
    assert hold["hold_request_id"] == request_id
    assert hold["booking_id"] == journey.booking_id
    assert hold["hold_records"] == 1, "the claim the demo makes is exactly one"
    assert hold["hold_expires_at"]


# ------------------------------------------------------------ authorization


async def test_authorization_comes_from_the_audit_trail_not_the_current_caller(
    journey: Fixture,
) -> None:
    """Who is authenticated now is a fact about now, not about a past action."""
    doc = await _document(journey)
    auth = doc["authorization"]
    assert auth["source"] == "traveler_access_audit"
    if auth["status"] != "unavailable":
        assert auth["decision"] in ("allow", "deny")
        assert auth["observed_at"]


async def test_authorization_evidence_is_the_decision_that_admitted_the_run(
    journey: Fixture,
) -> None:
    """Each read writes its own allow row; the evidence must not move with them."""
    first = await _document(journey)
    second = await _document(journey)
    assert second["authorization"]["status"] == "observed"
    assert second["authorization"]["audit_id"] == first["authorization"]["audit_id"]
    assert second["authorization"]["observed_at"] < first["observed_at"]
