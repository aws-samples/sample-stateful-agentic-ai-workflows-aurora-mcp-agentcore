"""Traveler identity and profile are row-scoped, against live Aurora.

`travelers` and `traveler_profiles` were granted to meridian_app with no RLS,
so the only thing keeping one traveler's session out of another traveler's
profile was the WHERE clause in each query. Reproduced live before migration
011: a session scoped to Alex, selecting from `travelers` with no predicate,
returned the decoy traveler as well.

`traveler_profiles` showed no leak in that reproduction, and that was the trap.
The decoy simply had no profile row, so the empty result reflected missing data
rather than a policy. This test gives the decoy a profile for its duration, so
the table can actually be observed leaking.

These run against the cluster and assert on what a scoped session can see,
because a fake has no row-level security to test.

Requires migration 011 and AWS credentials.
"""

from __future__ import annotations

from typing import AsyncIterator, Optional

import pytest
import pytest_asyncio

from backend.agentcore.identity import get_agentcore_identity
from backend.db.rds_data_client import get_rds_data_client

pytestmark = pytest.mark.database

ALEX = "trv_meridian_demo"
DECOY = "trv_demo_decoy"
PROBE_NOTE = "itest-profile-rls probe row"


@pytest_asyncio.fixture
async def decoy_profile() -> AsyncIterator[None]:
    """Give the decoy a profile row, removing it only if this fixture made it."""
    admin = get_rds_data_client()
    existing = await admin.execute(
        "SELECT 1 FROM traveler_profiles WHERE traveler_id = %s", (DECOY,)
    )
    created = not existing
    if created:
        await admin.execute(
            "INSERT INTO traveler_profiles (traveler_id, dietary_notes) VALUES (%s, %s)",
            (DECOY, PROBE_NOTE),
        )
    try:
        yield
    finally:
        if created:
            await admin.execute(
                "DELETE FROM traveler_profiles WHERE traveler_id = %s AND dietary_notes = %s",
                (DECOY, PROBE_NOTE),
            )


async def _visible_ids(table: str, traveler_id: str) -> list[str]:
    """Traveler ids a session scoped to `traveler_id` can see, with no WHERE."""
    db = get_rds_data_client()
    async with db.scoped_session(
        traveler_id=traveler_id,
        agent_type="concierge_agent",
        authorization=get_agentcore_identity().authorization_context(),
    ) as tx:
        rows = await db.execute(
            f"SELECT traveler_id FROM {table} ORDER BY traveler_id", (), transaction_id=tx
        )
    return [row["traveler_id"] for row in rows]


@pytest.mark.parametrize("table", ["travelers", "traveler_profiles"])
async def test_a_scoped_session_sees_only_its_own_traveler(table: str, decoy_profile) -> None:
    visible = await _visible_ids(table, ALEX)
    assert DECOY not in visible, (
        f"a session scoped to {ALEX} read {DECOY} from {table}; "
        f"the table is not row-scoped"
    )


@pytest.mark.parametrize("table", ["travelers", "traveler_profiles"])
async def test_the_traveler_still_sees_their_own_row(table: str, decoy_profile) -> None:
    """The positive control. Over-blocking here blanks the Traveler context panel."""
    assert ALEX in await _visible_ids(table, ALEX)


async def test_a_scoped_session_cannot_update_another_traveler(decoy_profile) -> None:
    """USING governs which rows an UPDATE can reach, not only which it can read.

    The assignment is a no-op on purpose, so that before the fix the test
    proves the row was reachable without actually changing the decoy's data.
    """
    db = get_rds_data_client()
    async with db.scoped_session(
        traveler_id=ALEX,
        agent_type="concierge_agent",
        authorization=get_agentcore_identity().authorization_context(),
    ) as tx:
        reached = await db.execute(
            "UPDATE travelers SET full_name = full_name WHERE traveler_id = %s "
            "RETURNING traveler_id",
            (DECOY,),
            transaction_id=tx,
        )
    assert not reached, f"a session scoped to {ALEX} could update {DECOY}'s traveler row"


async def _privilege(privilege: str) -> Optional[bool]:
    rows = await get_rds_data_client().execute(
        "SELECT has_table_privilege('meridian_app', 'agent_audit_log', %s) AS ok",
        (privilege,),
    )
    return rows[0]["ok"] if rows else None


@pytest.mark.parametrize("privilege", ["UPDATE", "DELETE"])
async def test_the_application_cannot_rewrite_its_audit_log(privilege: str) -> None:
    assert await _privilege(privilege) is False, (
        f"meridian_app holds {privilege} on agent_audit_log, so the actor can "
        f"change the record of its own actions"
    )


@pytest.mark.parametrize("privilege", ["INSERT", "SELECT"])
async def test_the_application_can_still_write_and_read_its_audit_log(privilege: str) -> None:
    """Append-only, not locked out: the agents still have to record what they did."""
    assert await _privilege(privilege) is True
