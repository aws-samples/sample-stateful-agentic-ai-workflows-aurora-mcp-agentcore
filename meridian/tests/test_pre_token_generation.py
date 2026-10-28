"""The sign-in trigger adds the bound traveler to the access token, or refuses the sign-in."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

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


IDENTITY_STACK = SOURCE.parents[2] / "lib" / "meridian-identity-stack.ts"
ACCESS_DENIED = ClientError(
    {"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "ExecuteStatement"
)
CONNECTION_ERRORS = [
    ConnectionClosedError(endpoint_url="https://rds-data.us-east-1.amazonaws.com"),
    EndpointConnectionError(endpoint_url="https://rds-data.us-east-1.amazonaws.com"),
    ConnectTimeoutError(endpoint_url="https://rds-data.us-east-1.amazonaws.com"),
]


class ScriptedDataApi:
    """Plays one outcome per call and moves the trigger's clock by that call's duration."""

    def __init__(self, clock, outcomes):
        self.clock, self.outcomes, self.calls = clock, list(outcomes), 0

    def execute_statement(self, **_request):
        self.calls += 1
        seconds, outcome = self.outcomes.pop(0)
        self.clock.now += seconds
        if isinstance(outcome, Exception):
            raise outcome
        return {"formattedRecords": json.dumps(outcome)}


class Clock:
    now = 100.0

    def monotonic(self):
        return self.now


@pytest.fixture
def scripted(trigger, monkeypatch):
    """Install a scripted Data API; the real retry client factory runs against a stubbed boto3."""
    clock, retry_configs = Clock(), []
    monkeypatch.setattr(trigger.time, "monotonic", clock.monotonic)

    def install(outcomes):
        api = ScriptedDataApi(clock, outcomes)

        def stub_client(service, config):
            retry_configs.append((service, config))
            return api

        trigger.RDS = api
        monkeypatch.setattr(trigger.boto3, "client", stub_client)
        return api

    install.retry_configs = retry_configs
    return install


ROW = [{"traveler_id": "trv_meridian_demo"}]


@pytest.mark.parametrize("error", CONNECTION_ERRORS, ids=lambda e: type(e).__name__)
def test_one_dropped_connection_is_retried_and_the_sign_in_passes(trigger, scripted, error):
    api = scripted([(0.05, error), (0.1, ROW)])
    result = trigger.lambda_handler(event(), None)
    assert api.calls == 2
    assert result["response"]["claimsAndScopeOverrideDetails"]["accessTokenGeneration"][
        "claimsToAddOrOverride"] == {"traveler_id": "trv_meridian_demo"}


def test_two_dropped_connections_refuse_with_the_same_text_and_code(trigger, scripted, caplog):
    api = scripted([(0.05, CONNECTION_ERRORS[0]), (0.05, CONNECTION_ERRORS[0])])
    with pytest.raises(RuntimeError) as raised:
        trigger.lambda_handler(event(sub="sub-secret-subject"), None)
    assert api.calls == 2
    assert str(raised.value) == "Sign-in refused: the traveler lookup failed."
    assert "Traveler lookup failed: ConnectionClosedError" in caplog.text
    assert "sub-secret-subject" not in caplog.text


@pytest.mark.parametrize("error", [
    ACCESS_DENIED,
    ClientError({"Error": {"Code": "BadRequestException", "Message": "x"}}, "ExecuteStatement"),
    ReadTimeoutError(endpoint_url="https://rds-data.us-east-1.amazonaws.com"),
    BotoCoreError(),
], ids=lambda e: type(e).__name__)
def test_an_answer_or_a_non_connection_failure_refuses_at_once_with_one_call(
    trigger, scripted, error
):
    api = scripted([(0.05, error), (0.05, ROW)])
    with pytest.raises(RuntimeError, match="the traveler lookup failed"):
        trigger.lambda_handler(event(), None)
    assert api.calls == 1


def test_a_slow_first_attempt_that_leaves_too_little_budget_is_not_retried(trigger, scripted):
    api = scripted([(3.2, CONNECTION_ERRORS[0]), (0.05, ROW)])
    with pytest.raises(RuntimeError, match="the traveler lookup failed"):
        trigger.lambda_handler(event(), None)
    assert api.calls == 1


@pytest.mark.parametrize("first_attempt_seconds", [0.05, 1.0, 2.1])
def test_the_retry_ends_well_before_the_function_and_cognito_limits(
    trigger, scripted, first_attempt_seconds
):
    """Fixed numbers, none read from the module: 4 s function timeout, 5 s Cognito limit."""
    scripted([(first_attempt_seconds, CONNECTION_ERRORS[2]), (0.1, ROW)])
    trigger.lambda_handler(event(), None)
    ((service, config),) = scripted.retry_configs
    worst_end = first_attempt_seconds + config.connect_timeout + config.read_timeout
    assert service == "rds-data"
    assert config.retries["total_max_attempts"] == 1
    assert worst_end <= 4.0 - 0.3 + 1e-9
    assert worst_end <= 5.0 - 1.0


def test_the_last_whole_second_attempt_starts_at_two_point_two_seconds(trigger, scripted):
    scripted([(2.1, CONNECTION_ERRORS[0]), (0.05, ROW)])
    assert trigger.lambda_handler(event(), None)["response"]
    ((_, config),) = scripted.retry_configs
    assert config.read_timeout >= 0.5


def test_a_first_attempt_just_past_two_point_two_seconds_is_not_retried(trigger, scripted):
    api = scripted([(2.3, CONNECTION_ERRORS[0]), (0.05, ROW)])
    with pytest.raises(RuntimeError, match="the traveler lookup failed"):
        trigger.lambda_handler(event(), None)
    assert api.calls == 1 and scripted.retry_configs == []


def test_the_budget_constant_matches_the_function_timeout_in_the_stack(trigger):
    stack = IDENTITY_STACK.read_text()
    assert f"timeout: Duration.seconds({int(trigger.FUNCTION_TIMEOUT_S)})" in stack


def test_the_function_timeout_is_four_seconds_in_the_stack_and_the_module(trigger):
    assert "timeout: Duration.seconds(4)," in IDENTITY_STACK.read_text()
    assert trigger.FUNCTION_TIMEOUT_S == 4
