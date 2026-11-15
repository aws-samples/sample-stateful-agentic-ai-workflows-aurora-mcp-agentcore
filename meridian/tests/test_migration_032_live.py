"""After migration 032, the booking write functions run as an owner that RLS still binds.

Run only after the migration is applied. Each check reads the catalog, or writes inside a
transaction that is rolled back, so nothing is committed.
"""

import pytest

from backend.db.rds_data_client import get_rds_data_client
from tests.rollback_support import uncommitted_transaction
from tests.test_migration_032_contract import OWNER

pytestmark = pytest.mark.database

JORDAN = "trv_meridian_demo"
OWNERS_SQL = (
    "SELECT p.proname, pg_get_userbyid(p.proowner) AS owner FROM pg_proc p "
    "WHERE p.proname IN ('create_courtesy_hold', 'confirm_booking', 'release_courtesy_hold')"
)
ROLE_SQL = (
    "SELECT rolcanlogin, rolbypassrls, rolsuper, rolinherit FROM pg_roles WHERE rolname = %s"
)
GRANTS_SQL = (
    "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
    "WHERE grantee = %s"
)
EXPECTED_GRANTS = {
    ("trip_packages", "SELECT"), ("journeys", "SELECT"),
    ("hold_requests", "SELECT"), ("hold_requests", "INSERT"),
    ("bookings", "SELECT"), ("bookings", "INSERT"),
    ("booking_lines", "SELECT"), ("booking_lines", "INSERT"),
}


async def test_the_three_write_functions_are_owned_by_the_new_role():
    rows = await get_rds_data_client().execute(OWNERS_SQL, ())
    assert {r["proname"]: r["owner"] for r in rows} == {
        "create_courtesy_hold": OWNER, "confirm_booking": OWNER, "release_courtesy_hold": OWNER,
    }


async def test_the_owner_cannot_log_in_bypass_rls_or_create_objects():
    client = get_rds_data_client()
    role = (await client.execute(ROLE_SQL, (OWNER,)))[0]
    assert (role["rolcanlogin"], role["rolbypassrls"], role["rolsuper"], role["rolinherit"]) == (
        False, False, False, False)
    rows = await client.execute(
        "SELECT has_schema_privilege(%s, 'public', 'CREATE') AS can_create", (OWNER,))
    assert rows[0]["can_create"] is False


async def test_the_owner_holds_exactly_the_table_privileges_it_needs():
    rows = await get_rds_data_client().execute(GRANTS_SQL, (OWNER,))
    assert {(r["table_name"], r["privilege_type"]) for r in rows} == EXPECTED_GRANTS


async def test_the_owner_may_update_only_the_status_and_confirmation_time():
    rows = await get_rds_data_client().execute(
        "SELECT column_name FROM information_schema.column_privileges "
        "WHERE grantee = %s AND table_name = 'bookings' AND privilege_type = 'UPDATE'", (OWNER,))
    assert {r["column_name"] for r in rows} == {"status", "confirmed_at"}


async def test_rls_refuses_a_booking_for_a_traveler_outside_the_scope():
    client = get_rds_data_client()
    with pytest.raises(Exception, match="row-level security"):
        async with uncommitted_transaction(client) as tx:
            await client.execute(f"SET LOCAL ROLE {OWNER}", (), transaction_id=tx)
            await client.execute(
                "SELECT set_config('app.current_traveler_id', %s, true)", (JORDAN,),
                transaction_id=tx)
            await client.execute(
                "SELECT set_config('app.agent_type', 'concierge_agent', true)", (),
                transaction_id=tx)
            await client.execute(
                "INSERT INTO bookings (booking_id, traveler_id, status, total_amount) "
                "VALUES ('HLD-RLSPROBE', 'trv_outside_scope', 'held', 1)", (),
                transaction_id=tx)


async def test_the_owner_still_sees_every_traveler_for_the_seat_count():
    client = get_rds_data_client()
    total = (await client.execute("SELECT count(*) AS n FROM bookings", ()))[0]["n"]
    async with uncommitted_transaction(client) as tx:
        await client.execute(f"SET LOCAL ROLE {OWNER}", (), transaction_id=tx)
        seen = (await client.execute("SELECT count(*) AS n FROM bookings", (),
                                     transaction_id=tx))[0]["n"]
    assert seen == total
