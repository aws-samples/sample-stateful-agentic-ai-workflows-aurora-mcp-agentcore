"""Migration 019 as applied: the workflow login holds UPDATE on one column, enough to lock a row."""

import pytest

from backend.db.rds_data_client import get_rds_data_client

pytestmark = pytest.mark.database


async def test_the_login_holds_update_on_the_lease_column_only():
    rows = await get_rds_data_client().execute(
        "SELECT a.attname AS name FROM pg_attribute a "
        "WHERE a.attrelid = 'public.journey_executions'::regclass AND a.attnum > 0 "
        "AND NOT a.attisdropped "
        "AND has_column_privilege('meridian_workflow', a.attrelid, a.attnum, 'UPDATE')"
    )
    assert [r["name"] for r in rows] == ["lease_expires_at"]


async def test_the_login_still_has_no_table_wide_update():
    rows = await get_rds_data_client().execute(
        "SELECT has_table_privilege('meridian_workflow', 'public.journey_executions', 'UPDATE') "
        "AS allowed"
    )
    assert rows == [{"allowed": False}]
