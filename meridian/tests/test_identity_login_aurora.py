"""meridian_identity against the live cluster: the sign-in trigger reads bindings only."""

import importlib.util
import os
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

from backend.db.rds_data_client import RDSDataClient, get_rds_data_client

pytestmark = pytest.mark.database

TRIGGER = (
    Path(__file__).resolve().parents[1]
    / "infra" / "functions" / "pre_token_generation" / "pre_token_generation.py"
)
JORDAN = "trv_meridian_demo"
DECOY = "trv_demo_decoy"


@pytest.fixture
def login() -> RDSDataClient:
    secret = os.environ.get("AURORA_IDENTITY_SECRET_ARN")
    if not secret:
        pytest.fail(
            "AURORA_IDENTITY_SECRET_ARN is not set: run scripts/provision_service_logins.py")
    return RDSDataClient(secret_arn=secret)


@pytest.fixture
def trigger(monkeypatch):
    """The sign-in trigger's own module, run against Aurora as the identity login."""
    spec = importlib.util.spec_from_file_location("pre_token_generation_as_identity", TRIGGER)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    # The module reads its settings when it is imported. Set them only for that moment: the
    # rest of the test must keep using the master client for its own setup and cleanup.
    with monkeypatch.context() as during_import:
        during_import.setenv("AURORA_SECRET_ARN", os.environ["AURORA_IDENTITY_SECRET_ARN"])
        during_import.setenv("AURORA_DATABASE", os.getenv("AURORA_DATABASE", "meridian"))
        during_import.setenv("AWS_DEFAULT_REGION", "us-east-1")
        spec.loader.exec_module(module)
    return module


@pytest_asyncio.fixture
async def subject():
    name = f"itest-{uuid.uuid4().hex[:10]}"
    yield name
    await get_rds_data_client().execute(
        "DELETE FROM traveler_identity_bindings WHERE subject_id = %s", (name,))


async def _bind(subject, traveler, status="active", expires="NULL"):
    await get_rds_data_client().execute(
        "INSERT INTO traveler_identity_bindings (binding_id, identity_provider, subject_id, "
        f"traveler_id, status, expires_at) VALUES (%s, 'cognito', %s, %s, %s, {expires})",
        (f"bind_{uuid.uuid4().hex[:16]}", subject, traveler, status))


def _event(subject):
    return {"version": "2", "triggerSource": "TokenGeneration_HostedAuth",
            "request": {"userAttributes": {"sub": subject}}, "response": {}}


def _claim(result):
    return result["response"]["claimsAndScopeOverrideDetails"]["accessTokenGeneration"][
        "claimsToAddOrOverride"]["traveler_id"]


async def test_an_active_binding_puts_its_traveler_in_the_token(trigger, subject):
    await _bind(subject, DECOY)
    assert _claim(trigger.lambda_handler(_event(subject), None)) == DECOY


@pytest.mark.parametrize(("status", "expires"), [
    ("revoked", "NULL"),
    ("active", "CURRENT_TIMESTAMP - interval '1 minute'"),
])
async def test_a_revoked_or_expired_binding_refuses_the_sign_in(trigger, subject, status, expires):
    await _bind(subject, JORDAN, status=status, expires=expires)
    with pytest.raises(RuntimeError, match="found 0"):
        trigger.lambda_handler(_event(subject), None)


async def test_a_user_with_no_binding_cannot_sign_in(trigger, subject):
    with pytest.raises(RuntimeError, match="found 0"):
        trigger.lambda_handler(_event(subject), None)


async def test_a_user_bound_to_two_travelers_cannot_sign_in(trigger, subject):
    await _bind(subject, JORDAN)
    await _bind(subject, DECOY)
    with pytest.raises(RuntimeError, match="found 2"):
        trigger.lambda_handler(_event(subject), None)


async def test_only_cognito_bindings_answer_a_cognito_subject(trigger, subject):
    await get_rds_data_client().execute(
        "INSERT INTO traveler_identity_bindings (binding_id, identity_provider, subject_id, "
        "traveler_id) VALUES (%s, 'aws_iam', %s, %s)",
        (f"bind_{uuid.uuid4().hex[:16]}", subject, JORDAN))
    with pytest.raises(RuntimeError, match="found 0"):
        trigger.lambda_handler(_event(subject), None)


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM trip_packages",
    "SELECT COUNT(*) FROM bookings",
    "SELECT COUNT(*) FROM traveler_access_audit",
    "SELECT backend_admin_count('rls_conversations')",
    "UPDATE traveler_identity_bindings SET status = status WHERE false",
    "INSERT INTO traveler_identity_bindings (binding_id, identity_provider, subject_id, "
    "traveler_id) VALUES ('x', 'cognito', 'forged', 'trv_meridian_demo')",
])
async def test_the_login_reads_the_bindings_and_nothing_else(login, sql):
    with pytest.raises(Exception, match="permission denied"):
        await login.execute(sql)


async def test_the_login_can_read_the_bindings(login):
    rows = await login.execute("SELECT COUNT(*) AS n FROM traveler_identity_bindings")
    assert rows[0]["n"] >= 0


@pytest.mark.parametrize("role", ["meridian_app", "meridian_backend", "meridian_admin"])
async def test_the_login_cannot_become_another_role(login, role):
    with pytest.raises(Exception, match="permission denied"):
        await login.execute(f"SET ROLE {role}")
