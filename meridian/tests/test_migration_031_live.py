"""After migration 031, Aurora holds the three limits for each workload login and no other.

Run only after the migration is applied. It reads pg_db_role_setting as the master login, then
asks a fresh Data API session as each login what it runs under. A pooled connection opened
before the migration keeps the old values until the Data API recycles it.
"""

import os

import pytest

from backend.db.rds_data_client import RDSDataClient, get_rds_data_client
from tests.test_migration_031_contract import EXPECTED, LOGINS

pytestmark = pytest.mark.database

SETTINGS_SQL = (
    "SELECT r.rolname, unnest(s.setconfig) AS setting FROM pg_db_role_setting s "
    "JOIN pg_roles r ON r.oid = s.setrole WHERE s.setdatabase = 0"
)


async def test_each_workload_login_holds_exactly_the_three_limits():
    rows = await get_rds_data_client().execute(SETTINGS_SQL, ())
    held: dict[str, list[str]] = {}
    for row in rows:
        held.setdefault(row["rolname"], []).append(row["setting"])
    want = sorted(f"{name}={value}" for name, value in EXPECTED.items())
    for login in LOGINS:
        assert sorted(held.get(login) or []) == want, login
    assert "meridian_admin" not in held
    assert "meridian_app" not in held


LOGIN_SECRETS = {
    "meridian_backend": "AURORA_BACKEND_SECRET_ARN",
    "meridian_gateway": "AURORA_GATEWAY_SECRET_ARN",
    "meridian_identity": "AURORA_IDENTITY_SECRET_ARN",
    "meridian_catalog": "AURORA_CATALOG_SECRET_ARN",
    "meridian_workflow": "AURORA_WORKFLOW_SECRET_ARN",
}
SESSION_SQL = (
    "SELECT current_user AS login, current_setting('statement_timeout') AS statement_timeout, "
    "current_setting('lock_timeout') AS lock_timeout, "
    "current_setting('idle_in_transaction_session_timeout') "
    "AS idle_in_transaction_session_timeout"
)


@pytest.mark.parametrize("login", LOGINS)
async def test_a_data_api_session_as_each_login_runs_under_the_limits(login):
    secret = os.environ.get(LOGIN_SECRETS[login])
    if not secret:
        pytest.fail(f"{LOGIN_SECRETS[login]} is not set: run scripts/provision_service_logins.py")
    row = (await RDSDataClient(secret_arn=secret).execute(SESSION_SQL, ()))[0]
    assert row["login"] == login
    assert {name: row[name] for name in EXPECTED} == {
        "statement_timeout": "30s", "lock_timeout": "10s",
        "idle_in_transaction_session_timeout": "2min",
    }
