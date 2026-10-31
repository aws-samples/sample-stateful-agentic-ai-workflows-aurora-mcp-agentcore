"""After migration 025, journeys keeps snapshot_store alone and an insert naming it works.

Run only after the migration is applied. Every statement runs as meridian_app with Jordan
pinned, inside a transaction that is rolled back, so nothing is committed.
"""

import uuid

import pytest

from backend.agentcore.identity import get_agentcore_identity
from backend.db.rds_data_client import get_rds_data_client
from tests.rollback_support import uncommitted_scope

pytestmark = pytest.mark.database

JORDAN = "trv_meridian_demo"
COLUMNS_SQL = (
    "SELECT column_name, is_nullable FROM information_schema.columns "
    "WHERE table_name = 'journeys' AND column_name IN ('snapshot_store', 'checkpoint_backend')"
)


async def test_only_snapshot_store_remains_and_it_is_required():
    client = get_rds_data_client()
    rows = await client.execute(COLUMNS_SQL, ())
    assert {(r["column_name"], r["is_nullable"]) for r in rows} == {("snapshot_store", "NO")}


async def test_an_insert_naming_snapshot_store_is_stored_as_given():
    client = get_rds_data_client()
    journey_id = f"jrn_t{uuid.uuid4().hex[:10]}"
    async with uncommitted_scope(
        client, traveler_id=JORDAN, agent_type="concierge_agent",
        authorization=get_agentcore_identity().authorization_context(),
    ) as tx:
        await client.execute(
            "INSERT INTO journeys (journey_id, traveler_id, snapshot_store) VALUES (%s, %s, %s)",
            (journey_id, JORDAN, "Aurora workflow_snapshots"), transaction_id=tx,
        )
        rows = await client.execute(
            "SELECT snapshot_store FROM journeys WHERE journey_id = %s", (journey_id,),
            transaction_id=tx,
        )
    assert rows[0]["snapshot_store"] == "Aurora workflow_snapshots"
