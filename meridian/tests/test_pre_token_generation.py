"""The sign-in trigger adds the bound traveler to the access token, or refuses the sign-in."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from botocore.exceptions import BotoCoreError, ClientError

SOURCE = (
    Path(__file__).resolve().parents[1]
    / "infra" / "functions" / "pre_token_generation" / "pre_token_generation.py"
)


@pytest.fixture
def trigger(monkeypatch):
    monkeypatch.setenv("AURORA_CLUSTER_ARN", "arn:aws:rds:us-east-1:123456789012:cluster:c")
    monkeypatch.setenv(
        "AURORA_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:123456789012:secret:identity-AbC123"
    )
    monkeypatch.setenv("AURORA_DATABASE", "meridian")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    spec = importlib.util.spec_from_file_location("pre_token_generation_under_test", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeDataApi:
    def __init__(self, rows):
        self.rows, self.requests = rows, []

    def execute_statement(self, **request):
        self.requests.append(request)
        return {"formattedRecords": json.dumps(self.rows)}


def event(sub="sub-jordan", version="2", source="TokenGeneration_HostedAuth"):
    return {
        "version": version,
        "triggerSource": source,
        "request": {"userAttributes": {"sub": sub, "email": "jordan@example.test"}},
        "response": {},
    }


def test_the_bound_traveler_is_added_to_the_access_token(trigger):
    trigger.RDS = FakeDataApi([{"traveler_id": "trv_meridian_demo"}])
    result = trigger.lambda_handler(event(), None)
    assert result["response"] == {"claimsAndScopeOverrideDetails": {"accessTokenGeneration": {
        "claimsToAddOrOverride": {"traveler_id": "trv_meridian_demo"}}}}


def test_the_lookup_is_by_cognito_subject_with_the_identity_secret(trigger):
    trigger.RDS = FakeDataApi([{"traveler_id": "trv_demo_decoy"}])
    trigger.lambda_handler(event(sub="sub-decoy"), None)
    (request,) = trigger.RDS.requests
    assert request["secretArn"].endswith("secret:identity-AbC123")
    assert {p["name"]: p["value"]["stringValue"] for p in request["parameters"]} == {
        "provider": "cognito", "subject": "sub-decoy"}
    assert "status = 'active'" in request["sql"] and "expires_at" in request["sql"]


@pytest.mark.parametrize("source", [
    "TokenGeneration_HostedAuth", "TokenGeneration_Authentication",
    "TokenGeneration_RefreshTokens", "TokenGeneration_NewPasswordChallenge",
])
def test_every_token_generation_source_gets_the_claim(trigger, source):
    trigger.RDS = FakeDataApi([{"traveler_id": "trv_meridian_demo"}])
    result = trigger.lambda_handler(event(source=source), None)
    claims = result["response"]["claimsAndScopeOverrideDetails"]["accessTokenGeneration"]
    assert claims["claimsToAddOrOverride"]["traveler_id"] == "trv_meridian_demo"


def test_a_user_with_no_active_binding_cannot_sign_in(trigger):
    trigger.RDS = FakeDataApi([])
    with pytest.raises(RuntimeError, match="exactly one active traveler binding, found 0"):
        trigger.lambda_handler(event(), None)


def test_a_user_bound_to_two_travelers_cannot_sign_in(trigger):
    trigger.RDS = FakeDataApi([{"traveler_id": "trv_a"}, {"traveler_id": "trv_b"}])
    with pytest.raises(RuntimeError, match="found 2"):
        trigger.lambda_handler(event(), None)


def test_an_event_without_a_subject_is_refused_before_any_lookup(trigger):
    trigger.RDS = FakeDataApi([{"traveler_id": "trv_meridian_demo"}])
    with pytest.raises(RuntimeError, match="no user sub"):
        trigger.lambda_handler({"version": "2", "request": {"userAttributes": {}}}, None)
    assert trigger.RDS.requests == []


@pytest.mark.parametrize("version", ["1", "3", None])
def test_only_event_version_two_can_customize_an_access_token(trigger, version):
    trigger.RDS = FakeDataApi([{"traveler_id": "trv_meridian_demo"}])
    with pytest.raises(RuntimeError, match="event version 2"):
        trigger.lambda_handler(event(version=version), None)
    assert trigger.RDS.requests == []


class FailingDataApi:
    def __init__(self, error):
        self.error = error

    def execute_statement(self, **_request):
        raise self.error


CLUSTER_ARN = "arn:aws:rds:us-east-1:123456789012:cluster:c"


@pytest.mark.parametrize("error", [
    ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": f"not allowed on {CLUSTER_ARN}"}},
        "ExecuteStatement",
    ),
    BotoCoreError(),
])
def test_a_data_api_failure_does_not_leak_to_the_user(trigger, error, caplog):
    trigger.RDS = FailingDataApi(error)
    with pytest.raises(RuntimeError) as raised:
        trigger.lambda_handler(event(), None)
    assert str(raised.value) == "Sign-in refused: the traveler lookup failed."
    assert raised.value.__suppress_context__ and raised.value.__cause__ is None
    assert "arn:aws" not in str(raised.value)
    assert "arn:aws" not in caplog.text


def test_the_data_api_client_has_short_timeouts_and_one_attempt(trigger):
    config = trigger.RDS.meta.config
    assert (config.connect_timeout, config.read_timeout) == (1, 3)
    assert config.retries["total_max_attempts"] == 1
