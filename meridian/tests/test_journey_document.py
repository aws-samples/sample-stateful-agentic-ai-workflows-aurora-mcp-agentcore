"""GET /journeys/{journey_id} assembles evidence from what Aurora actually holds.

The document is what Presenter proof renders. Its whole value is that every
claim is traceable: each section names the source it came from, the ids it
describes, and when it was observed, and evidence that does not exist says so
rather than being quietly omitted or inferred from prose.

Runs against the live cluster. Requires migrations 007 and 008.
"""

from __future__ import annotations

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
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            await self.client.execute(
                f"DELETE FROM {table} WHERE thread_id = %s", (self.thread_id,)
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
        fx.journey_id = await create_journey(db, TRAVELER, "AuroraDataApiSaver")
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
    assert doc["checkpoint_backend"]["kind"] == "AuroraDataApiSaver"
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


async def test_a_committed_checkpoint_is_reported_with_its_thread(
    journey: Fixture,
) -> None:
    from backend.db.aurora_dataapi_saver import AuroraDataApiSaver

    saver = AuroraDataApiSaver(journey.client)
    config = {"configurable": {"thread_id": journey.thread_id, "checkpoint_ns": ""}}
    await saver.aput(
        config,
        {
            "v": 1,
            "id": "cp_doc_01",
            "ts": "2026-09-06T02:13:41+00:00",
            "channel_values": {"selected_package": "TKY-003"},
            "channel_versions": {"selected_package": "1"},
            "versions_seen": {},
        },
        {"step": 1},
        {"selected_package": "1"},
    )

    doc = await _document(journey)
    assert doc["checkpoint"]["status"] == "committed"
    assert doc["checkpoint"]["checkpoint_id"] == "cp_doc_01"
    assert doc["checkpoint"]["thread_id"] == journey.thread_id
    assert doc["checkpoint"]["source"] == "checkpoints"
    assert doc["checkpoint"]["committed_at"].startswith("2026-09-06")

    plan = doc["selected_plan"]
    assert plan["package_id"] == "TKY-003"
    assert journey.thread_id in plan["source"]
    assert "cp_doc_01" in plan["source"]


# --------------------------------------------------------------------- hold


async def test_the_hold_is_reported_as_one_hold_for_this_request(
    journey: Fixture,
) -> None:
    from backend.agents.orchestration_05.hold_intent import (
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
