"""The rollback puts the saved configuration back, in reverse order, reading each hop back."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from scripts.identity_release import lambda_release, rollback, settings, snapshot
from tests import release_support as rs
from tests import snapshot_support as ss
from tests.aws_recorders import client_error

ORDER = ["update_function", "publish_function", "update_response_headers_policy",
         "update_service", "update_gateway", "update_agent_runtime", "update_agent_runtime",
         "put_parameter", "update_function_configuration", "update_function_configuration"]
DESTRUCTIVE = ("delete", "remove", "terminate", "deregister", "detach")


def released(tmp_path, manual=True, mode="iam"):
    """A world saved in ``mode``, then released; returns the world and the saved snapshot."""
    world = ss.SnapWorld(tmp_path, mode=mode)
    saved = json.loads(json.dumps(ss.taken(world)))
    before = world.state()
    world.release(manual=manual)
    return world, saved, before


def context(world, saved, apply=True, sleeps=None):
    return rollback.Context(
        saved=saved, clients=ss.clients_of(world), where=ss.where_of(), apply=apply,
        sleep=(sleeps.append if sleeps is not None else (lambda seconds: None)),
        stamp="20261008T123005Z")


def go(world, saved, **kwargs):
    said: list[str] = []
    outcome = rollback.run(context(world, saved, **kwargs), said.append)
    return outcome, said


# ----------------------------------------------------------------------- restoring


def test_every_restorable_hop_is_back_to_what_was_saved(tmp_path):
    world, saved, before = released(tmp_path)

    outcome, _ = go(world, saved)

    assert world.state() == before
    assert world.violations() == []
    assert [r.name for r in outcome.results if r.status == "restored"] == [
        "site", "service", "gateway", "runtime MeridianConcierge", "runtime MeridianWorkflow",
        "lambda secret parameter", "lambda holds", "lambda semantic"]


def test_the_writes_run_in_the_reverse_of_the_release_order(tmp_path):
    world, saved, _ = released(tmp_path)

    go(world, saved)

    assert world.writes() == ORDER


def test_each_step_is_read_back_right_after_its_write(tmp_path):
    world, saved, _ = released(tmp_path)

    go(world, saved)

    log = [(kind, op) for kind, _, op in world.log]
    for write, read in [("update_service", "describe_service"), ("update_gateway", "get_gateway"),
                        ("update_agent_runtime", "get_agent_runtime"),
                        ("put_parameter", "get_parameter"),
                        ("update_function_configuration", "get_function_configuration"),
                        ("update_response_headers_policy", "get_response_headers_policy"),
                        ("publish_function", "get_function")]:
        index = log.index(("write", write))
        assert ("read", read) in log[index + 1:], f"{write} was not read back"


def test_the_gateway_update_is_complete_and_valid(tmp_path):
    world, saved, _ = released(tmp_path)

    go(world, saved)

    sent = world.clients["bedrock-agentcore-control"].calls
    request = next(kw for op, kw in sent if op == "update_gateway")
    assert request["authorizerType"] == "AWS_IAM" and "interceptorConfigurations" not in request
    for field in ("name", "roleArn", "description", "protocolType", "protocolConfiguration",
                  "exceptionLevel", "policyEngineConfiguration"):
        assert request[field] == saved["gateway"][field]
    assert request["gatewayIdentifier"] == rs.GATEWAY_ID


def test_a_gateway_saved_with_the_interceptor_gets_it_back(tmp_path):
    world, saved, before = released(tmp_path, mode="jwt")
    world.gateway = ss.runtime_gateway("iam")

    go(world, saved)

    request = next(kw for op, kw in world.clients["bedrock-agentcore-control"].calls
                   if op == "update_gateway")
    assert request["authorizerType"] == "CUSTOM_JWT"
    assert request["interceptorConfigurations"] == saved["gateway"]["interceptorConfigurations"]
    assert world.gateway == before["gateway"]


def test_the_runtimes_get_their_artifact_authorizer_headers_and_environment_back(tmp_path):
    world, saved, _ = released(tmp_path)

    go(world, saved)

    requests = [kw for op, kw in world.clients["bedrock-agentcore-control"].calls
                if op == "update_agent_runtime"]
    assert len(requests) == 2
    for request in requests:
        assert "authorizerConfiguration" not in request
        assert request["agentRuntimeArtifact"]["containerConfiguration"]["containerUri"].endswith(
            "old")
        assert request["environmentVariables"]["MERIDIAN_AGENTCORE_AUTH"] == "iam"


def test_the_service_gets_its_image_environment_and_secret_reference_back(tmp_path):
    world, saved, _ = released(tmp_path)

    go(world, saved)

    request = next(kw for op, kw in world.clients["apprunner"].calls if op == "update_service")
    config = request["SourceConfiguration"]["ImageRepository"]
    assert config["ImageIdentifier"] == "ecr/meridian:old"
    assert config["ImageConfiguration"]["RuntimeEnvironmentSecrets"] == {
        "MERIDIAN_API_TOKEN": ss.API_SECRET}
    assert request["InstanceConfiguration"]["InstanceRoleArn"].endswith("-instance")
    assert request["ServiceArn"] == ss.SERVICE_ARN


def test_the_edge_function_and_the_policy_are_restored_and_published(tmp_path):
    world, saved, _ = released(tmp_path)

    go(world, saved)

    assert world.fn["live"]["code"] == ss.IAM_CODE
    assert world.rhp["config"]["SecurityHeadersConfig"]["ContentSecurityPolicy"][
        "ContentSecurityPolicy"] == "default-src 'self'"


def test_the_holds_lambda_restarts_with_a_marker_and_keeps_the_saved_variables(tmp_path):
    world, saved, _ = released(tmp_path)

    go(world, saved)

    variables = world.lambdas[ss.HOLDS_ARN]["Environment"]["Variables"]
    assert variables[lambda_release.MARKER] == "20261008T123005Z"
    assert variables["AURORA_SECRET_ARN"] == rs.MASTER_SECRET and variables["POOL_SIZE"] == "4"


def test_nothing_is_ever_deleted(tmp_path):
    world, saved, _ = released(tmp_path)

    go(world, saved)

    names = [op for client in world.clients.values() for op, _ in client.calls]
    assert not [n for n in names if n.startswith(DESTRUCTIVE)]


# --------------------------------------------------- what the API cannot restore


def test_the_roles_stack_and_the_rules_are_reported_with_the_exact_commands(tmp_path):
    world, saved, _ = released(tmp_path)

    outcome, said = go(world, saved)

    manual = {r.name: r for r in outcome.results if r.status == "manual"}
    assert set(manual) == {"roles stack", "agentcore stack"}
    text = "\n".join(said)
    assert "publish.py" in text and "agentcore deploy -y" in text
    assert "MERIDIAN_AGENTCORE_AUTH=iam" in text
    assert rs.ACCOUNT not in text and outcome.code == 1


def test_a_release_with_only_restorable_changes_finishes_clean(tmp_path):
    world, saved, before = released(tmp_path, manual=False)

    outcome, said = go(world, saved)

    assert outcome.code == 0 and world.state() == before
    assert said[-1].startswith("Rollback complete")


# ------------------------------------------------------------------ idempotence


def test_running_it_twice_changes_nothing_the_second_time(tmp_path):
    world, saved, before = released(tmp_path, manual=False)
    go(world, saved)
    writes_after_first = len(world.writes())

    outcome, _ = go(world, saved)

    assert len(world.writes()) == writes_after_first
    assert outcome.code == 0 and world.state() == before
    assert {r.status for r in outcome.results} == {"unchanged"}


def test_a_world_that_was_never_released_is_left_alone(tmp_path):
    world = ss.SnapWorld(tmp_path)
    saved = json.loads(json.dumps(ss.taken(world)))

    outcome, _ = go(world, saved)

    assert world.writes() == [] and outcome.code == 0


# --------------------------------------------------------------------- dry run


def test_a_dry_run_reads_everything_writes_nothing_and_says_what_would_change(tmp_path):
    world, saved, _ = released(tmp_path)
    after_release = world.state()

    outcome, said = go(world, saved, apply=False)

    assert world.writes() == [] and world.state() == after_release
    text = "\n".join(said)
    assert "would restore" in text and "authorizerType" in text
    assert "MERIDIAN_API_TOKEN" in text
    assert outcome.code == 0


def test_a_dry_run_prints_no_secret_value_and_no_account_id(tmp_path):
    world, saved, _ = released(tmp_path)

    _, said = go(world, saved, apply=False)

    text = "\n".join(said)
    assert not any(secret in text for secret in ss.PLANTED) and "e" + "yJ" not in text


# --------------------------------------------------------------------- failures


def test_a_failed_step_is_reported_and_the_others_still_run(tmp_path):
    world, saved, before = released(tmp_path, manual=False)
    world.failures["update_service"] = client_error("ServiceUnavailable", f"{rs.ACCOUNT} down")

    outcome, said = go(world, saved)

    failed = [r for r in outcome.results if r.status == "failed"]
    assert [r.name for r in failed] == ["service"] and outcome.code == 1
    assert "update_gateway" in world.writes() and "put_parameter" in world.writes()
    text = "\n".join(said)
    assert rs.ACCOUNT not in text and "ServiceUnavailable" in text
    restored = world.state()
    assert restored["gateway"] == before["gateway"] and restored["service"] != before["service"]


def test_a_second_run_finishes_what_a_failed_run_left(tmp_path):
    world, saved, before = released(tmp_path, manual=False)
    world.failures["update_gateway"] = client_error("ThrottlingException")
    go(world, saved)

    outcome, _ = go(world, saved)

    assert outcome.code == 0 and world.state() == before


def test_a_write_that_does_not_take_is_a_failure_not_a_success(tmp_path):
    world, saved, _ = released(tmp_path, manual=False)
    control = world.clients["bedrock-agentcore-control"]

    def ignored(**kwargs):
        control.calls.append(("update_gateway", kwargs))
        world.log.append(("write", "bedrock-agentcore-control", "update_gateway"))

    control.update_gateway = ignored

    outcome, said = go(world, saved)

    assert [r.name for r in outcome.results if r.status == "failed"] == ["gateway"]
    assert outcome.code == 1 and "did not take" in "\n".join(said)


def test_a_wait_that_never_finishes_ends_instead_of_hanging(tmp_path):
    world, saved, _ = released(tmp_path, manual=False)
    world.update_takes = 10 ** 6
    sleeps: list[float] = []

    outcome, said = go(world, saved, sleeps=sleeps)

    assert [r.name for r in outcome.results if r.status == "failed"] == ["service"]
    assert 0 < len(sleeps) <= rollback.WAIT_ATTEMPTS * 8
    assert "still" in "\n".join(said)


def test_a_saved_copy_with_a_redacted_value_is_not_restored_for_that_hop(tmp_path):
    world = ss.SnapWorld(tmp_path)
    world.runtimes[rs.RUNTIME_IDS["MeridianWorkflow"]]["environmentVariables"]["API_KEY"] = "k-1"
    saved = json.loads(json.dumps(ss.taken(world)))
    world.release(manual=False)

    outcome, said = go(world, saved)

    failed = [r for r in outcome.results if r.status == "failed"]
    assert [r.name for r in failed] == ["runtime MeridianWorkflow"]
    assert "redacted" in "\n".join(said) and "API_KEY" in "\n".join(said)
    assert world.writes().count("update_agent_runtime") == 1


def test_a_lambda_saved_with_a_redacted_value_is_not_restored(tmp_path):
    world = ss.SnapWorld(tmp_path)
    world.lambdas[ss.HOLDS_ARN]["Environment"]["Variables"]["SERVICE_TOKEN"] = "t-1"
    world.lambdas[lambda_release.SEMANTIC_FUNCTION]["Environment"]["Variables"]["DB_PASSWORD"] = "p"
    saved = json.loads(json.dumps(ss.taken(world)))
    world.release(manual=False)

    outcome, said = go(world, saved)

    failed = [r.name for r in outcome.results if r.status == "failed"]
    assert failed == ["lambda holds", "lambda semantic"]
    assert world.writes().count("update_function_configuration") == 0
    assert "SERVICE_TOKEN" in "\n".join(said)


def test_a_gateway_of_another_id_than_the_saved_one_is_refused(tmp_path):
    world, saved, _ = released(tmp_path)
    saved["gateway"]["gatewayId"] = "someone-elses-gateway"

    outcome, _ = go(world, saved)

    assert "gateway" in [r.name for r in outcome.results if r.status == "failed"]
    assert "update_gateway" not in world.writes()


def test_a_service_arn_in_another_account_is_refused(tmp_path):
    world, saved, _ = released(tmp_path)
    arn = saved["service"]["ServiceArn"]
    saved["service"]["ServiceArn"] = arn.replace(rs.ACCOUNT, "999999999999")

    outcome, _ = go(world, saved)

    assert "service" in [r.name for r in outcome.results if r.status == "failed"]
    assert "update_service" not in world.writes()


def test_a_holds_function_that_is_not_this_projects_is_refused(tmp_path):
    world, saved, _ = released(tmp_path)
    saved["lambdas"]["holds"]["arn"] = ss.SEMANTIC_ARN

    outcome, _ = go(world, saved)

    assert "lambda holds" in [r.name for r in outcome.results if r.status == "failed"]


# ----------------------------------------------------------------- the checks it keeps


def test_the_snapshot_is_checked_against_the_deployment_before_anything_is_built():
    saved = {"account": "999999999999", "region": rs.REGION}

    with pytest.raises(settings.ReleaseConfigError, match="account"):
        rollback.require_same_deployment(saved, rs.ACCOUNT, rs.REGION)
    with pytest.raises(settings.ReleaseConfigError, match="Region"):
        rollback.require_same_deployment({"account": rs.ACCOUNT, "region": "eu-west-1"},
                                         rs.ACCOUNT, rs.REGION)
    rollback.require_same_deployment({"account": rs.ACCOUNT, "region": rs.REGION},
                                     rs.ACCOUNT, rs.REGION)


def test_the_rollback_error_never_shows_an_account_id():
    error = snapshot.SnapshotError("x")

    assert isinstance(error, settings.ReleaseConfigError)


def test_the_saved_snapshot_is_not_altered_by_a_rollback(tmp_path):
    world, saved, _ = released(tmp_path)
    copy = deepcopy(saved)

    go(world, saved)

    assert saved == copy
