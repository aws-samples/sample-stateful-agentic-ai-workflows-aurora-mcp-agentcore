"""Migration 024 as applied: an insert or update naming one store column fills the other.

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
READ_SQL = "SELECT checkpoint_backend, snapshot_store FROM journeys WHERE journey_id = %s"


async def run(insert_sql, params, update_sql=None, update_params=()):
    """Insert one journey, optionally update it, read both columns back and roll back."""
    client = get_rds_data_client()
    journey_id = f"jrn_t{uuid.uuid4().hex[:10]}"
    async with uncommitted_scope(
        client, traveler_id=JORDAN, agent_type="concierge_agent",
        authorization=get_agentcore_identity().authorization_context(),
    ) as tx:
        await client.execute(insert_sql, (journey_id, JORDAN, *params), transaction_id=tx)
        if update_sql:
            await client.execute(update_sql, (*update_params, journey_id), transaction_id=tx)
        rows = await client.execute(READ_SQL, (journey_id,), transaction_id=tx)
    return rows[0]["checkpoint_backend"], rows[0]["snapshot_store"]


async def test_an_insert_naming_only_the_old_column_fills_the_new_one():
    sql = "INSERT INTO journeys (journey_id, traveler_id, checkpoint_backend) VALUES (%s, %s, %s)"
    assert await run(sql, ("old-store",)) == ("old-store", "old-store")


async def test_an_insert_naming_only_the_new_column_fills_the_old_one():
    sql = "INSERT INTO journeys (journey_id, traveler_id, snapshot_store) VALUES (%s, %s, %s)"
    assert await run(sql, ("new-store",)) == ("new-store", "new-store")


async def test_an_update_of_either_column_moves_the_other():
    insert = "INSERT INTO journeys (journey_id, traveler_id, snapshot_store) VALUES (%s, %s, %s)"
    old = "UPDATE journeys SET checkpoint_backend = %s WHERE journey_id = %s"
    new = "UPDATE journeys SET snapshot_store = %s WHERE journey_id = %s"
    assert await run(insert, ("a",), old, ("b",)) == ("b", "b")
    assert await run(insert, ("a",), new, ("c",)) == ("c", "c")
