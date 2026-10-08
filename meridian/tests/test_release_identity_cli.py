"""`release_identity.py check` reads every hop back and exits non-zero on any drift."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from scripts import release_identity
from scripts.identity_release import interceptor_lambda, settings
from tests import release_support as rs
from tests.aws_recorders import Recorder, Waiters, client_error

FAKE_TOKEN = "e" + "yJ" + "abc.def.ghi"
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
CLUSTER = f"arn:aws:rds:{rs.REGION}:{rs.ACCOUNT}:cluster:meridian"
SERVICE_ARN = rs.SERVICE_ARN
IDENTITY_OUTPUTS = rs.identity_outputs()
SKIP = "--skip-service"


def env(**extra):
    return {
        "AURORA_CLUSTER_ARN": CLUSTER, "MERIDIAN_AGENTCORE_AUTH": "jwt", **rs.COGNITO_ENV,
        "AURORA_SECRET_ARN": rs.MASTER_SECRET, "AURORA_BACKEND_SECRET_ARN": rs.BACKEND_SECRET,
        "VITE_COGNITO_DOMAIN": rs.HOSTED_UI_DOMAIN,
        "AGENTCORE_GATEWAY_URL": f"https://{rs.GATEWAY_ID}.gateway.bedrock-agentcore."
                                 f"{rs.REGION}.amazonaws.com/mcp",
        "AGENTCORE_RUNTIME_ARN": f"arn:aws:bedrock-agentcore:{rs.REGION}:{rs.ACCOUNT}:runtime/"
                                 + rs.RUNTIME_IDS["MeridianConcierge"],
        "AGENTCORE_WORKFLOW_RUNTIME_ARN": f"arn:aws:bedrock-agentcore:{rs.REGION}:{rs.ACCOUNT}:"
                                          "runtime/" + rs.RUNTIME_IDS["MeridianWorkflow"],
        **extra,
    }


class LambdaClient(Recorder, Waiters):
    def __init__(self, **kwargs):
        Recorder.__init__(self, **kwargs)
        Waiters.__init__(self)


def deployed_configuration(**variables):
    wanted = interceptor_lambda.desired(
        rs.ACCOUNT, rs.REGION, settings.cognito_settings(rs.COGNITO_ENV))
    return {"FunctionName": wanted.function_name,
            "Environment": {"Variables": {**wanted.environment, **variables}}}


class World:
    """Recording fake clients for every service the commands may use."""

    def __init__(self, mode="jwt", account=rs.ACCOUNT, service=None):
        self.sts = Mock()
        self.sts.get_caller_identity.return_value = {"Account": account}
        self.control = Mock()
        self.control.get_gateway.return_value = rs.gateway(mode)
        self.control.list_gateways.return_value = {"items": [
            {"gatewayId": rs.GATEWAY_ID, "name": rs.gateway(mode)["name"]}]}
        runtimes = {rs.RUNTIME_IDS[n]: rs.runtime(n, mode) for n in rs.RUNTIME_IDS}
        self.control.get_agent_runtime.side_effect = lambda agentRuntimeId: runtimes[agentRuntimeId]
        names = rs.BASE_POLICIES + ([rs.BINDING_POLICY] if mode == "jwt" else [])
        self.control.list_policies.return_value = {"policies": rs.policies(names)}
        self.cfn = Mock()
        self.cfn.describe_stacks.return_value = {"Stacks": [{"Outputs": IDENTITY_OUTPUTS}]}
        self.apprunner = Mock()
        self.apprunner.describe_service.return_value = {"Service": service or {}}
        self.iam = Recorder({
            "list_role_policies": {"PolicyNames": ["runtime-policy"]},
            "get_role_policy": {"PolicyDocument": {"Version": "2012-10-17", "Statement": [{
                "Effect": "Allow", "Action": "bedrock-agentcore:InvokeGateway",
                "Resource": "*"}]}},
            "list_attached_role_policies": {"AttachedPolicies": []}})
        self.lam = LambdaClient(answers={"get_function_configuration": deployed_configuration()})

    def session(self, region):
        clients = {"sts": self.sts, "bedrock-agentcore-control": self.control,
                   "cloudformation": self.cfn, "apprunner": self.apprunner,
                   "iam": self.iam, "lambda": self.lam}
        return Mock(client=lambda name, **kwargs: clients[name])


def proof(tmp_path, **fields):
    path = tmp_path / "backend-login-proof.json"
    path.write_text(json.dumps(rs.receipt(NOW, **fields)))
    return path


def run(argv, world, tmp_path, environment=None, with_proof=True, sha=rs.SHA, **fields):
    deps = release_identity.Dependencies(
        env=environment or env(), session=world.session, now=lambda: NOW,
        head_sha=lambda: sha,
        proof_path=proof(tmp_path, **fields) if with_proof else tmp_path / "none.json",
        release_dir=tmp_path / "release", sleep=lambda seconds: None)
    return release_identity.main(argv, deps)


def test_a_release_that_matches_its_mode_reports_ok_and_exits_zero(tmp_path, capsys):
    assert run(["check", SKIP], World("jwt"), tmp_path) == 0

    assert "OK" in capsys.readouterr().out


def test_check_compares_the_live_gateway_with_the_mode_and_never_reads_the_template(
        tmp_path, capsys):
    """The live Gateway is judged against the mode in `.env`; an unreadable rendered file
    changes nothing."""
    folder = tmp_path / "project" / "agentcore"
    folder.mkdir(parents=True)
    (folder / "agentcore.json").write_text("not json at all")
    world = World("jwt")
    deps = release_identity.Dependencies(
        env=env(), session=world.session, now=lambda: NOW, head_sha=lambda: rs.SHA,
        proof_path=proof(tmp_path), release_dir=tmp_path / "release",
        agentcore_dir=tmp_path / "project")

    assert release_identity.main(["check", SKIP], deps) == 0

    assert "OK" in capsys.readouterr().out


def test_the_gateway_is_read_by_the_modes_name(tmp_path, capsys):
    world = World("jwt")

    assert run(["check", SKIP], world, tmp_path) == 0

    assert world.control.list_gateways.called
    world.control.get_gateway.assert_called_with(gatewayIdentifier=rs.GATEWAY_ID)


def test_a_replaced_gateway_leaves_the_env_url_stale_and_check_says_how_to_refresh(
        tmp_path, capsys):
    world = World("jwt")
    world.control.list_gateways.return_value = {"items": [
        {"gatewayId": "meridianv2-meridian-aurora-jwt-new0000000",
         "name": settings.gateway_physical_name("jwt")}]}

    assert run(["check", SKIP], world, tmp_path) == 1

    out = capsys.readouterr().out
    assert ("DRIFT  Settings: AGENTCORE_GATEWAY_URL names another Gateway than the one called "
            "meridianv2-meridian-aurora-jwt; run scripts/sync_agentcore_env.py --write") in out
    world.control.get_gateway.assert_called_with(
        gatewayIdentifier="meridianv2-meridian-aurora-jwt-new0000000")


def test_a_mode_with_no_gateway_of_its_name_is_one_finding_and_not_a_crash(tmp_path, capsys):
    world = World("jwt")
    world.control.list_gateways.return_value = {"items": [
        {"gatewayId": rs.GATEWAY_ID, "name": settings.gateway_physical_name("iam")}]}

    assert run(["check", SKIP], world, tmp_path) == 1

    out = capsys.readouterr().out
    assert "DRIFT  Gateway: no Gateway named meridianv2-meridian-aurora-jwt exists" in out
    world.control.get_gateway.assert_not_called()


def test_check_in_iam_mode_reads_the_runtime_roles_for_the_found_gateway(tmp_path, capsys):
    world = World("iam")

    assert run(["check", "--expect", "iam", SKIP], world, tmp_path, with_proof=False) == 0

    assert world.iam.names()


def test_every_drifted_hop_is_printed_and_the_exit_is_one(tmp_path, capsys):
    world = World("iam")
    world.control.list_gateways.return_value = {"items": [
        {"gatewayId": rs.GATEWAY_ID, "name": settings.gateway_physical_name("jwt")}]}

    assert run(["check", SKIP], world, tmp_path) == 1

    out = capsys.readouterr().out
    assert "DRIFT  Gateway: name is 'meridianv2-meridian-aurora', expected" in out
    assert "DRIFT  Gateway: authorizer is AWS_IAM, expected CUSTOM_JWT" in out
    assert "DRIFT  Runtime MeridianConcierge: has no JWT authorizer" in out
    assert "DRIFT  Runtime MeridianWorkflow: " in out
    assert "OK" not in out


def test_expect_iam_reads_the_baseline_even_when_the_env_says_jwt(tmp_path, capsys):
    assert run(["check", "--expect", "iam", SKIP], World("iam"), tmp_path, with_proof=False) == 0

    assert "every hop reports iam" in capsys.readouterr().out


def test_the_jwt_check_also_wants_the_identity_stack_and_the_backend_login_proof(tmp_path, capsys):
    world = World("jwt")
    world.cfn.describe_stacks.return_value = {"Stacks": [{"Outputs": []}]}

    assert run(["check", SKIP], world, tmp_path, with_proof=False) == 1

    out = capsys.readouterr().out
    assert "Identity stack: has no output" in out
    assert "Backend login proof: none recorded" in out


def test_credentials_for_another_account_stop_before_any_read_with_exit_two(tmp_path, capsys):
    world = World("jwt", account="999999999999")

    assert run(["check", SKIP], world, tmp_path) == 2

    captured = capsys.readouterr()
    assert "<acct>" in captured.err and "999999999999" not in captured.err
    assert "Traceback" not in captured.err and captured.out == ""
    world.control.get_gateway.assert_not_called()


def test_a_missing_setting_exits_two_and_says_which(tmp_path, capsys):
    environment = env()
    del environment["AGENTCORE_WORKFLOW_RUNTIME_ARN"]

    assert run(["check", SKIP], World("jwt"), tmp_path, environment) == 2

    assert "AGENTCORE_WORKFLOW_RUNTIME_ARN" in capsys.readouterr().err


def test_a_bad_mode_exits_two(tmp_path, capsys):
    assert run(["check", SKIP], World("jwt"), tmp_path, env(MERIDIAN_AGENTCORE_AUTH="true")) == 2

    assert "MERIDIAN_AGENTCORE_AUTH" in capsys.readouterr().err


def live_service(variables, secrets=None):
    return rs.app_runner_service(variables, secrets)


def test_the_live_service_environment_is_checked_when_the_service_is_named(tmp_path, capsys):
    good = World("jwt", service=live_service(rs.jwt_service_variables()))
    assert run(["check", "--service-arn", SERVICE_ARN], good, tmp_path) == 0
    good.apprunner.describe_service.assert_called_once_with(ServiceArn=SERVICE_ARN)

    bad = World("jwt", service=live_service(
        rs.jwt_service_variables(), {"MERIDIAN_API_TOKEN": "arn:secret"}))
    assert run(["check", "--service-arn", SERVICE_ARN], bad, tmp_path) == 1
    assert "Service: MERIDIAN_API_TOKEN is still a secret reference" in capsys.readouterr().out


def test_a_service_running_the_master_login_is_drift(tmp_path, capsys):
    variables = {**rs.jwt_service_variables(), "AURORA_SECRET_ARN": rs.MASTER_SECRET,
                 "AURORA_BACKEND_SECRET_ARN": rs.MASTER_SECRET}
    world = World("jwt", service=live_service(variables))

    assert run(["check", "--service-arn", SERVICE_ARN], world, tmp_path) == 1

    assert "master" in capsys.readouterr().out


def test_without_a_service_arn_or_skip_the_service_is_not_checked_and_the_exit_is_four(
        tmp_path, capsys):
    assert run(["check"], World("jwt"), tmp_path) == 4

    out = capsys.readouterr().out
    assert "NOT CHECKED  App Runner service" in out and "--skip-service" in out
    assert "OK" not in out


def test_skipping_the_service_on_purpose_is_the_only_way_to_a_zero_without_it(tmp_path, capsys):
    assert run(["check", SKIP], World("jwt"), tmp_path) == 0

    assert "App Runner service skipped" in capsys.readouterr().out


def test_drift_wins_over_the_not_checked_exit(tmp_path, capsys):
    assert run(["check"], World("iam"), tmp_path) == 1

    out = capsys.readouterr().out
    assert "DRIFT" in out and "NOT CHECKED" in out


def test_a_service_arn_and_skip_service_together_are_refused(tmp_path, capsys):
    with pytest.raises(SystemExit) as stopped:
        run(["check", "--service-arn", SERVICE_ARN, SKIP], World("jwt"), tmp_path)

    assert stopped.value.code == release_identity.EXIT_USAGE


@pytest.mark.parametrize("arn", [
    SERVICE_ARN.replace(rs.ACCOUNT, "999999999999"),
    SERVICE_ARN.replace(rs.REGION, "eu-west-1"),
    "arn:aws:apprunner:us-east-1:123456789012:service/other/abc123",
])
def test_a_service_arn_for_another_account_or_region_is_drift_and_is_not_read(
        tmp_path, capsys, arn):
    world = World("jwt", service=live_service(rs.jwt_service_variables()))

    assert run(["check", "--service-arn", arn], world, tmp_path) == 1

    out = capsys.readouterr().out
    assert "--service-arn" in out and "999999999999" not in out
    world.apprunner.describe_service.assert_not_called()


def test_a_code_based_service_is_a_finding_not_a_crash(tmp_path, capsys):
    world = World("jwt", service={"SourceConfiguration": {
        "CodeRepository": {"RepositoryUrl": "https://example.com/repo"}}})

    assert run(["check", "--service-arn", SERVICE_ARN], world, tmp_path) == 1

    captured = capsys.readouterr()
    assert "DRIFT  Service: not an image-based source" in captured.out
    assert "Traceback" not in captured.err


def test_a_hop_setting_for_another_region_is_drift(tmp_path, capsys):
    other = (f"arn:aws:bedrock-agentcore:eu-west-1:{rs.ACCOUNT}:runtime/"
             + rs.RUNTIME_IDS["MeridianConcierge"])

    assert run(["check", SKIP], World("jwt"), tmp_path, env(AGENTCORE_RUNTIME_ARN=other)) == 1

    assert "DRIFT  Settings: AGENTCORE_RUNTIME_ARN names another Region" in capsys.readouterr().out


@pytest.mark.parametrize(("fields", "sha", "word"), [
    ({"account": "999999999999"}, rs.SHA, "account"),
    ({}, "f" * 40, "git_sha"),
    ({"ok": "no"}, rs.SHA, "did not pass"),
    ({"at": "2026-10-08T11:00:00"}, rs.SHA, "time zone"),
])
def test_a_receipt_for_another_release_is_drift(tmp_path, capsys, fields, sha, word):
    assert run(["check", SKIP], World("jwt"), tmp_path, sha=sha, **fields) == 1

    out = capsys.readouterr().out
    assert "DRIFT  Backend login proof: " in out and word in out and "999999999999" not in out


def test_an_identity_stack_that_disagrees_with_the_site_setting_is_drift(tmp_path, capsys):
    environment = env(VITE_COGNITO_DOMAIN="other.auth.example.com")

    assert run(["check", SKIP], World("jwt"), tmp_path, environment) == 1

    assert "HostedUiDomain" in capsys.readouterr().out


def test_an_issuer_output_that_is_not_the_pools_is_drift(tmp_path, capsys):
    world = World("jwt")
    outputs = [dict(o) for o in IDENTITY_OUTPUTS]
    outputs[3]["OutputValue"] = "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Other"
    world.cfn.describe_stacks.return_value = {"Stacks": [{"Outputs": outputs}]}

    assert run(["check", SKIP], world, tmp_path) == 1

    assert "output Issuer differs" in capsys.readouterr().out


def test_an_unexpected_error_exits_two_naming_only_its_type(tmp_path, capsys):
    world = World("jwt")
    world.control.get_gateway.side_effect = RuntimeError(f"boom for {rs.ACCOUNT} eyJabc")

    assert run(["check", SKIP], world, tmp_path) == 2

    captured = capsys.readouterr()
    assert captured.err.strip() == "error: unexpected (RuntimeError)"
    assert "Traceback" not in captured.err and rs.ACCOUNT not in captured.err


def test_garbage_from_the_control_plane_is_drift_not_a_crash(tmp_path, capsys):
    world = World("jwt")
    world.control.get_gateway.return_value = None

    assert run(["check", SKIP], world, tmp_path) == 1

    assert "DRIFT  Gateway: " in capsys.readouterr().out


def test_the_cedar_only_design_expects_no_interceptor(tmp_path, capsys):
    world = World("jwt")
    world.control.get_gateway.return_value = rs.gateway("jwt", settings.CEDAR)

    assert run(["check", SKIP], world, tmp_path, env(MERIDIAN_GATEWAY_ENFORCEMENT="cedar")) == 0
    assert run(["check", SKIP], world, tmp_path, env()) == 1
    assert "no request interceptor" in capsys.readouterr().out


def test_an_aws_read_failure_exits_two_without_a_traceback_or_an_account_id(tmp_path, capsys):
    from botocore.exceptions import ClientError

    world = World("jwt")
    denied = ClientError(
        {"Error": {"Code": "AccessDeniedException",
                   "Message": f"User arn:aws:iam::{rs.ACCOUNT}:user/x may not read"}},
        "GetGateway")
    world.control.get_gateway.side_effect = denied

    assert run(["check", SKIP], world, tmp_path) == 2

    err = capsys.readouterr().err
    assert "AccessDeniedException" in err and "Traceback" not in err and rs.ACCOUNT not in err


def test_check_makes_only_read_calls(tmp_path):
    world = World("jwt")

    run(["check", "--service-arn", SERVICE_ARN], world, tmp_path)

    called = {c[0] for mock in (world.control, world.cfn, world.apprunner, world.sts)
              for c in mock.method_calls} | set(world.lam.names())
    assert called <= {"get_gateway", "list_gateways", "get_agent_runtime", "list_policies",
                      "describe_stacks", "describe_service", "get_caller_identity",
                      # lambda:GetFunctionConfiguration reads the interceptor's environment
                      "get_function_configuration"}
    assert "get_function_configuration" in called


# --------------------------------------------------------------- interceptor

TAGS = interceptor_lambda.TAGS


def interceptor_world(exists=False):
    """A World whose interceptor function exists (or does not) and matches what is wanted."""
    wanted = interceptor_lambda.desired(
        rs.ACCOUNT, rs.REGION, settings.cognito_settings(rs.COGNITO_ENV))
    configuration = {
        "FunctionName": wanted.function_name, "State": "Active",
        "CodeSha256": wanted.code_sha256, "Handler": interceptor_lambda.HANDLER,
        "Runtime": interceptor_lambda.RUNTIME, "Role": wanted.role_arn,
        "Timeout": interceptor_lambda.TIMEOUT_SECONDS, "MemorySize": interceptor_lambda.MEMORY_MB,
        "Environment": {"Variables": wanted.environment}}
    pairs = [{"Key": k, "Value": v} for k, v in TAGS.items()]
    world = World("jwt")
    world.iam = Recorder({
        "get_role": {"Role": {
            "Arn": wanted.role_arn, "Tags": pairs,
            "AssumeRolePolicyDocument": json.loads(wanted.trust_policy)}},
        "list_role_policies": {"PolicyNames": [interceptor_lambda.POLICY_NAME]},
        "get_role_policy": {"PolicyDocument": wanted.role_policy},
        "list_attached_role_policies": {"AttachedPolicies": []}})
    missing = {} if exists else {
        "get_function": [client_error("ResourceNotFoundException")]}
    world.lam = LambdaClient(
        answers={"get_function": {"Configuration": configuration, "Tags": dict(TAGS)},
                 "create_function": configuration,
                 "get_function_configuration": configuration},
        failures=missing)
    return world


def test_the_interceptor_command_is_a_dry_run_unless_both_flags_are_given(tmp_path, capsys):
    world = interceptor_world()

    assert run(["interceptor"], world, tmp_path) == 0

    out = capsys.readouterr().out
    assert "DRY RUN" in out and "IAM role meridian-gateway-traveler-pin" in out
    assert world.iam.calls == [] and world.lam.calls == []
    assert settings.CONFIRM_FLAG in out and rs.ACCOUNT not in out
    assert not (tmp_path / "release").exists()


def test_apply_without_the_confirmation_changes_nothing_and_exits_three(tmp_path, capsys):
    world = interceptor_world()

    assert run(["interceptor", "--apply"], world, tmp_path) == 3

    assert settings.CONFIRM_FLAG in capsys.readouterr().out
    assert world.iam.calls == [] and world.lam.calls == []


def test_apply_deploys_the_function_then_reads_it_back(tmp_path, capsys):
    world = interceptor_world()

    assert run(["interceptor", "--apply", settings.CONFIRM_FLAG], world, tmp_path) == 0

    out = capsys.readouterr().out
    assert "create_function" in world.lam.names()
    assert "Lambda meridian-gateway-traveler-pin: created" in out
    assert "OK  the interceptor function matches" in out
    assert rs.ACCOUNT not in out
    saved = tmp_path / "release" / interceptor_lambda.OUTPUT_NAME
    assert oct(saved.stat().st_mode & 0o777) == "0o600" and rs.ACCOUNT not in saved.read_text()


def test_apply_reports_drift_the_read_back_finds_and_exits_one(tmp_path, capsys):
    world = interceptor_world(exists=True)
    world.iam.answers["list_attached_role_policies"] = {
        "AttachedPolicies": [{"PolicyName": "AdministratorAccess"}]}

    assert run(["interceptor", "--apply", settings.CONFIRM_FLAG], world, tmp_path) == 1

    assert "DRIFT  IAM role meridian-gateway-traveler-pin: has the managed policy" in (
        capsys.readouterr().out)
    assert not (tmp_path / "release" / interceptor_lambda.OUTPUT_NAME).exists()


def test_the_interceptor_command_refuses_the_cedar_only_design(tmp_path, capsys):
    world = interceptor_world()

    assert run(["interceptor"], world, tmp_path, env(MERIDIAN_GATEWAY_ENFORCEMENT="cedar")) == 2

    assert "cedar" in capsys.readouterr().err and world.lam.calls == []


def test_credentials_for_another_account_stop_the_interceptor_command_with_exit_two(
        tmp_path, capsys):
    world = interceptor_world()
    world.sts.get_caller_identity.return_value = {"Account": "999999999999"}

    assert run(["interceptor", "--apply", settings.CONFIRM_FLAG], world, tmp_path) == 2

    assert "999999999999" not in capsys.readouterr().err
    assert world.lam.calls == [] and world.iam.calls == []


def test_an_aws_failure_during_apply_is_masked_and_has_no_traceback(tmp_path, capsys):
    world = interceptor_world()
    world.iam.failures["get_role"] = [client_error(
        "AccessDenied", f"not allowed on arn:aws:iam::{rs.ACCOUNT}:role/x token {FAKE_TOKEN}")]

    assert run(["interceptor", "--apply", settings.CONFIRM_FLAG], world, tmp_path) == 2

    err = capsys.readouterr().err
    assert "AccessDenied" in err and "<acct>" in err and "<token>" in err
    assert rs.ACCOUNT not in err and FAKE_TOKEN not in err and "Traceback" not in err


def test_a_foreign_function_makes_apply_exit_two_and_change_nothing(tmp_path, capsys):
    world = interceptor_world(exists=True)
    world.lam.answers["get_function"]["Tags"] = {"project": "other"}

    assert run(["interceptor", "--apply", settings.CONFIRM_FLAG], world, tmp_path) == 2

    assert "not tagged" in capsys.readouterr().err
    assert "update_function_code" not in world.lam.names()


def test_the_delete_command_is_a_dry_run_unless_both_flags_are_given(tmp_path, capsys):
    world = interceptor_world(exists=True)

    assert run(["interceptor-delete"], world, tmp_path) == 0
    assert "DRY RUN" in capsys.readouterr().out
    assert run(["interceptor-delete", "--apply"], world, tmp_path) == 3
    assert world.iam.calls == [] and world.lam.calls == []


def test_the_delete_command_removes_only_the_tagged_resources_and_the_outputs(tmp_path, capsys):
    world = interceptor_world(exists=True)
    release = tmp_path / "release"
    release.mkdir()
    (release / interceptor_lambda.OUTPUT_NAME).write_text("{}")

    assert run(["interceptor-delete", "--apply", settings.CONFIRM_FLAG], world, tmp_path) == 0

    assert "delete_function" in world.lam.names() and "delete_role" in world.iam.names()
    assert not (release / interceptor_lambda.OUTPUT_NAME).exists()
    assert rs.ACCOUNT not in capsys.readouterr().out


def test_the_delete_command_works_whatever_the_enforcement_design(tmp_path):
    world = interceptor_world(exists=True)

    assert run(["interceptor-delete", "--apply", settings.CONFIRM_FLAG], world, tmp_path,
               env(MERIDIAN_GATEWAY_ENFORCEMENT="cedar")) == 0


def test_abbreviated_flags_are_not_accepted(tmp_path):
    with pytest.raises(SystemExit):
        run(["interceptor", "--app"], interceptor_world(), tmp_path)


# ---------------------------------------------------------------- usage errors


def test_the_exit_codes_are_the_documented_table():
    assert (release_identity.EXIT_DRIFT, release_identity.EXIT_COULD_NOT_RUN,
            release_identity.EXIT_USAGE, release_identity.EXIT_NOT_CHECKED) == (1, 2, 3, 4)
    assert release_identity.EXIT_REFUSED == release_identity.EXIT_USAGE


@pytest.mark.parametrize("argv", [
    ["interceptor", rs.ACCOUNT, FAKE_TOKEN],
    ["check", "--expect", rs.ACCOUNT + FAKE_TOKEN],
    ["check", rs.ACCOUNT, FAKE_TOKEN],
    [rs.ACCOUNT, FAKE_TOKEN],
])
def test_a_usage_error_exits_three_and_echoes_neither_an_account_id_nor_a_token(
        tmp_path, capsys, argv):
    with pytest.raises(SystemExit) as stopped:
        run(argv, World("jwt"), tmp_path)

    captured = capsys.readouterr()
    assert stopped.value.code == 3
    for stream in (captured.err, captured.out):
        assert rs.ACCOUNT not in stream and FAKE_TOKEN not in stream
    assert "error:" in captured.err and ("<acct>" in captured.err or "<token>" in captured.err)


def test_help_still_exits_zero(tmp_path):
    with pytest.raises(SystemExit) as stopped:
        run(["--help"], World("jwt"), tmp_path)

    assert stopped.value.code == 0


def test_every_usage_line_names_the_real_confirmation_flag():
    text = release_identity.__doc__ + release_identity.build_parser().format_help()
    for command in ("interceptor", "interceptor-delete"):
        assert f"{command} [--apply {settings.CONFIRM_FLAG}]" in text
    assert "--apply FLAG" not in text


# ----------------------------------------------- the interceptor's environment in check


def test_a_deployed_interceptor_with_the_right_environment_is_not_drift(tmp_path, capsys):
    world = World("jwt")

    assert run(["check", SKIP], world, tmp_path) == 0

    assert world.lam.args("get_function_configuration") == [
        {"FunctionName": settings.interceptor_arn(rs.ACCOUNT, rs.REGION)}]


def test_a_pinned_tools_override_on_the_interceptor_is_drift(tmp_path, capsys):
    world = World("jwt")
    world.lam.answers["get_function_configuration"] = deployed_configuration(PINNED_TOOLS="x")

    assert run(["check", SKIP], world, tmp_path) == 1

    assert "DRIFT  Interceptor Lambda: PINNED_TOOLS is set" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["EXPECTED_CLIENT_ID", "EXPECTED_ISSUER"])
def test_an_interceptor_that_trusts_another_client_or_issuer_is_drift(tmp_path, capsys, name):
    world = World("jwt")
    world.lam.answers["get_function_configuration"] = deployed_configuration(**{name: "other"})

    assert run(["check", SKIP], world, tmp_path) == 1

    out = capsys.readouterr().out
    assert f"{name} is not the pool's" in out and "other" not in out


def test_an_interceptor_with_no_environment_is_drift_naming_both_variables(tmp_path, capsys):
    world = World("jwt")
    world.lam.answers["get_function_configuration"] = {"FunctionName": "x"}

    assert run(["check", SKIP], world, tmp_path) == 1

    out = capsys.readouterr().out
    assert "EXPECTED_CLIENT_ID is not set" in out and "EXPECTED_ISSUER is not set" in out


def test_a_missing_interceptor_is_reported_as_not_deployed(tmp_path, capsys):
    world = World("jwt")
    world.lam = LambdaClient(failures={
        "get_function_configuration": client_error("ResourceNotFoundException")})

    assert run(["check", SKIP], world, tmp_path) == 1

    assert "DRIFT  Interceptor Lambda: not deployed" in capsys.readouterr().out


def test_an_unreadable_interceptor_configuration_is_drift_not_a_crash(tmp_path, capsys):
    world = World("jwt")
    world.lam.answers["get_function_configuration"] = {"Environment": "garbage"}

    assert run(["check", SKIP], world, tmp_path) == 1

    assert "Interceptor Lambda" in capsys.readouterr().out


def test_the_cedar_only_design_does_not_read_the_lambda(tmp_path):
    world = World("jwt")
    world.control.get_gateway.return_value = rs.gateway("jwt", settings.CEDAR)

    assert run(["check", SKIP], world, tmp_path, env(MERIDIAN_GATEWAY_ENFORCEMENT="cedar")) == 0

    assert world.lam.calls == []


def test_the_iam_mode_does_not_read_the_lambda(tmp_path):
    world = World("iam")

    assert run(["check", "--expect", "iam", SKIP], world, tmp_path, with_proof=False) == 0

    assert world.lam.calls == []


def test_an_access_denied_on_the_lambda_read_exits_two_masked(tmp_path, capsys):
    world = World("jwt")
    world.lam = LambdaClient(failures={"get_function_configuration": client_error(
        "AccessDeniedException", f"arn:aws:iam::{rs.ACCOUNT}:user/x")})

    assert run(["check", SKIP], world, tmp_path) == 2

    err = capsys.readouterr().err
    assert "AccessDeniedException" in err and rs.ACCOUNT not in err
