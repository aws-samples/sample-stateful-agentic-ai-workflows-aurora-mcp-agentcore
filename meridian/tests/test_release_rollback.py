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


def context(world, saved, apply=True, sleeps=None, gateway_id=rs.GATEWAY_ID):
    return rollback.Context(
        saved=saved, clients=ss.clients_of(world), where=ss.where_of(gateway_id), apply=apply,
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
    world.gateway = {**ss.runtime_gateway("iam"), "name": saved["gateway"]["name"]}

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


def test_a_gateway_of_another_id_than_the_saved_one_is_never_written(tmp_path):
    world, saved, _ = released(tmp_path)
    saved["gateway"]["gatewayId"] = "someone-elses-gateway"

    outcome, _ = go(world, saved)

    assert "gateway" not in [r.name for r in outcome.results if r.status == "restored"]
    assert not [w for w in world.writes() if w in API_WRITES]


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


# ----------------------------------------- a Gateway that a stack deploy replaced

API_WRITES = ("update_gateway", "update_agent_runtime", "update_service")
NEW_ID = ss.NEW_GATEWAY_ID


def replaced_by_the_release(tmp_path):
    """Saved in iam; the release then replaced the iam Gateway with the jwt one."""
    world, saved, before = released(tmp_path)
    world.replace_gateway("jwt")
    return world, saved, before


def test_a_gateway_the_release_replaced_is_not_restored_and_no_environment_is_sent(tmp_path):
    world, saved, _ = replaced_by_the_release(tmp_path)

    outcome, said = go(world, saved, gateway_id=NEW_ID)

    by_name = {r.name: r.status for r in outcome.results}
    for hop in ("gateway", "runtime MeridianConcierge", "runtime MeridianWorkflow", "service"):
        assert by_name[hop] == "manual", hop
    assert not [w for w in world.writes() if w in API_WRITES]
    assert outcome.code == 1
    assert "replaced" in "\n".join(said)


def test_the_hops_that_do_not_depend_on_the_gateway_are_still_restored_by_api(tmp_path):
    world, saved, before = replaced_by_the_release(tmp_path)

    outcome, _ = go(world, saved, gateway_id=NEW_ID)

    restored = {r.name for r in outcome.results if r.status == "restored"}
    assert restored == {"site", "lambda secret parameter", "lambda holds", "lambda semantic"}
    assert world.state()["parameters"] == before["parameters"]


def test_a_gateway_that_is_gone_is_reported_as_missing_not_as_an_aws_error(tmp_path):
    world, saved, _ = replaced_by_the_release(tmp_path)

    outcome, said = go(world, saved, gateway_id=NEW_ID)

    gateway = next(r for r in outcome.results if r.name == "gateway")
    assert gateway.status == "manual"
    assert any("not restorable here" in line and "no Gateway named" in line
               for line in gateway.lines)
    assert "ResourceNotFound" not in "\n".join(said)


def test_a_recreated_gateway_with_a_new_id_role_and_engine_counts_as_the_saved_one(tmp_path):
    world = ss.SnapWorld(tmp_path)
    saved = json.loads(json.dumps(ss.taken(world)))
    world.replace_gateway("iam", "meridianv2-meridian-aurora-recreated1")

    outcome, _ = go(world, saved, gateway_id="meridianv2-meridian-aurora-recreated1")

    by_name = {r.name: r.status for r in outcome.results}
    for hop in ("gateway", "runtime MeridianConcierge", "runtime MeridianWorkflow", "service"):
        assert by_name[hop] == "unchanged", hop
    assert not [w for w in world.writes() if w in API_WRITES]
    assert outcome.code == 0 and by_name["agentcore stack"] == "unchanged"


def test_a_recreated_gateway_that_differs_in_a_real_field_is_reported_with_both_values(tmp_path):
    world = ss.SnapWorld(tmp_path)
    saved = json.loads(json.dumps(ss.taken(world)))
    world.replace_gateway("iam", "recreated-1")
    world.gateway["exceptionLevel"] = "NONE"
    assert world.gateway["roleArn"] != saved["gateway"]["roleArn"]

    outcome, _ = go(world, saved, gateway_id="recreated-1")

    gateway = next(r for r in outcome.results if r.name == "gateway")
    assert gateway.status == "manual"
    assert any("exceptionLevel: " in line and "NONE" in line and "DEBUG" in line
               for line in gateway.lines)
    assert not any("roleArn" in line or "policyEngineConfiguration.arn" in line
                   for line in gateway.lines)
    assert "update_gateway" not in world.writes()


def test_a_service_still_naming_a_dead_gateway_is_not_restored_and_points_at_publish(tmp_path):
    world, saved, _ = replaced_by_the_release(tmp_path)

    outcome, said = go(world, saved, gateway_id=NEW_ID)

    service = next(r for r in outcome.results if r.name == "service")
    assert service.status == "manual"
    assert "publish.py" in "\n".join(said)
    assert "update_service" not in world.writes()


def test_the_replaced_gateway_remedy_lists_the_four_stage_deploys_and_what_follows(tmp_path):
    world, saved, _ = replaced_by_the_release(tmp_path)

    outcome, said = go(world, saved, gateway_id=NEW_ID)

    text = "\n".join(said)
    assert "MERIDIAN_AGENTCORE_AUTH=iam python scripts/render_agentcore_config.py" in text
    assert ("python scripts/release_identity.py deploy --to iam --apply "
            "--i-understand-this-changes-aws") in text
    assert "Configuration complete." in text
    for word in ("gateway, targets, governance, complete", "NEW id and URL",
                 "scripts/bind_gateway_workload.py", "scripts/sync_agentcore_env.py --write",
                 "scripts/publish.py", "release_identity.py check --expect iam"):
        assert word in text, word
    assert "UpdateGateway API first" not in text and "restored the Gateway to" not in text
    assert [r for r in outcome.results if r.name == "agentcore stack"][0].status == "manual"


def test_a_gateway_to_be_replaced_that_carries_the_interceptor_gets_a_revoke_line(tmp_path):
    world, saved, _ = replaced_by_the_release(tmp_path)

    _, said = go(world, saved, gateway_id=NEW_ID)

    assert ("python scripts/release_identity.py gateway --to jwt --only revoke --apply "
            "--i-understand-this-changes-aws") in "\n".join(said)


def test_no_revoke_line_when_the_gateway_to_be_replaced_has_no_interceptor(tmp_path):
    world, saved, _ = replaced_by_the_release(tmp_path)
    world.gateway.pop("interceptorConfigurations", None)

    _, said = go(world, saved, gateway_id=NEW_ID)

    assert "--only revoke" not in "\n".join(said)


def test_the_holds_step_with_a_gateway_id_that_no_longer_exists_says_to_sync_the_settings(tmp_path):
    world, saved, _ = replaced_by_the_release(tmp_path)

    outcome, said = go(world, saved)

    holds = next(r for r in outcome.results if r.name == "lambda holds")
    assert holds.status == "failed"
    assert "sync_agentcore_env.py --write" in "\n".join(holds.lines)
    assert "ResourceNotFound" not in "\n".join(said)


def test_a_dry_run_of_a_replaced_gateway_writes_nothing_and_still_prints_the_remedy(tmp_path):
    world, saved, _ = replaced_by_the_release(tmp_path)

    outcome, said = go(world, saved, apply=False, gateway_id=NEW_ID)

    assert world.writes() == [] and outcome.code == 0
    assert "render_agentcore_config.py" in "\n".join(said)


def test_an_unreplaced_gateway_keeps_the_api_restore_and_its_old_remedy(tmp_path):
    world, saved, before = released(tmp_path)

    outcome, said = go(world, saved)

    assert world.state()["gateway"] == before["gateway"]
    assert "replaced" not in "\n".join(said)
    assert "release_identity.py deploy --to iam" in "\n".join(said)
    assert "through the UpdateGateway API" not in "\n".join(said)


def test_a_service_that_still_names_the_dead_gateway_is_held_back_even_when_all_else_matches(
        tmp_path):
    world = ss.SnapWorld(tmp_path)
    saved = json.loads(json.dumps(ss.taken(world)))
    world.replace_gateway("iam", "meridianv2-meridian-aurora-recreated1")
    config = world.service["SourceConfiguration"]["ImageRepository"]["ImageConfiguration"]
    config["RuntimeEnvironmentVariables"]["AGENTCORE_GATEWAY_URL"] = rs.GATEWAY_URL

    outcome, said = go(world, saved, gateway_id="meridianv2-meridian-aurora-recreated1")

    service = next(r for r in outcome.results if r.name == "service")
    assert service.status == "manual"
    assert any("does not name the live Gateway" in line for line in service.lines)
    assert "update_service" not in world.writes()


def test_the_stack_hops_are_read_through_the_live_gateway_not_the_stale_settings_id(tmp_path):
    world = ss.SnapWorld(tmp_path)
    saved = json.loads(json.dumps(ss.taken(world)))
    world.replace_gateway("iam", "meridianv2-meridian-aurora-recreated1")

    outcome, _ = go(world, saved, gateway_id=rs.GATEWAY_ID)

    stack = next(r for r in outcome.results if r.name == "agentcore stack")
    assert stack.status == "unchanged", stack.lines


def test_any_other_error_reading_the_holds_target_is_not_blamed_on_the_settings(tmp_path):
    world, saved, _ = released(tmp_path, manual=False)
    world.failures["list_gateway_targets"] = client_error("AccessDeniedException", "no")

    outcome, _ = go(world, saved)

    holds = next(r for r in outcome.results if r.name == "lambda holds")
    assert holds.status == "failed"
    assert any("AccessDeniedException" in line for line in holds.lines)
    assert not any("sync_agentcore_env" in line for line in holds.lines)
