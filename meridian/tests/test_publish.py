"""Established deployments must fail closed without deleting or rotating access."""
import json
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from scripts import publish
from scripts.identity_release import settings
from tests import release_support as rs


RUNTIME_ID = "meridianv2_MeridianWorkflow-x"
WORKFLOW_ARN = f"arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/{RUNTIME_ID}"
CONCIERGE_ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example"
GATEWAY_URL = "https://gw1.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"


def hosted_environment(**overrides):
    environment = {
        "AURORA_CLUSTER_ARN": "arn:aws:rds:us-east-1:123456789012:cluster:example",
        "AURORA_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:example-abcdef",
        "AGENTCORE_RUNTIME_ARN": CONCIERGE_ARN,
        "AGENTCORE_WORKFLOW_RUNTIME_ARN": WORKFLOW_ARN,
        "AGENTCORE_GATEWAY_URL": GATEWAY_URL,
    }
    environment.update(overrides)
    return environment


def existing():
    return {"Status": "RUNNING", "ServiceArn": "service", "InstanceConfiguration": {"Cpu": "1024", "Memory": "2048"},
            "SourceConfiguration": {"ImageRepository": {"ImageIdentifier": "old", "ImageConfiguration": {
                "Port": "8000", "StartCommand": "custom", "RuntimeEnvironmentVariables": {
                    "MERIDIAN_API_TOKEN": "legacy-placeholder", "EXISTING_SETTING": "preserve"},
                "RuntimeEnvironmentSecrets": {"OTHER_SECRET": "other-reference"}}}}}


def test_release_uses_secret_reference_without_mutating_input():
    service = existing()
    before = deepcopy(service)
    definition = publish.service_definition(service, "new", {"AWS_REGION": "us-east-1"},
                                           {"AccessRoleArn": "pull", "InstanceRoleArn": "task"}, "token-reference")
    config = definition["SourceConfiguration"]["ImageRepository"]["ImageConfiguration"]
    assert "MERIDIAN_API_TOKEN" not in config["RuntimeEnvironmentVariables"]
    assert config["RuntimeEnvironmentSecrets"] == {"OTHER_SECRET": "other-reference", "MERIDIAN_API_TOKEN": "token-reference"}
    assert config["RuntimeEnvironmentVariables"]["EXISTING_SETTING"] == "preserve"
    assert config["StartCommand"] == "custom"
    assert service == before


@pytest.mark.parametrize("status", ["CREATE_FAILED", "DELETE_FAILED", "OPERATION_IN_PROGRESS", "PAUSED"])
def test_release_never_repairs_a_service_by_deleting_it(status):
    service, client = existing(), Mock()
    service["Status"] = status
    with pytest.raises(RuntimeError, match="refusing"):
        publish.update_service(client, service, {})
    client.update_service.assert_not_called()
    client.delete_service.assert_not_called()


def test_running_rollback_is_not_accepted_as_deployed(monkeypatch):
    service, client = existing(), Mock()
    client.update_service.return_value = {"OperationId": "update"}
    client.describe_service.return_value = {"Service": service}
    monkeypatch.setattr(publish, "wait_for_operation", Mock())
    with pytest.raises(RuntimeError, match="parity failed"):
        publish.update_service(client, service, {"SourceConfiguration": {"ImageRepository": {"ImageIdentifier": "new"}}})
    client.delete_service.assert_not_called()


@pytest.mark.parametrize("actual,region,arn", [
    ("999999999999", "us-east-1", "arn:aws:apprunner:us-east-1:123456789012:service/meridian-web/abc"),
    ("123456789012", "us-west-2", "arn:aws:apprunner:us-east-1:123456789012:service/meridian-web/abc"),
    ("123456789012", "us-east-1", "arn:aws:apprunner:us-east-1:123456789012:service/other/abc"),
])
def test_target_mismatch_stops_before_mutation(actual, region, arn):
    with pytest.raises(ValueError):
        publish.validate_target("123456789012", region, arn, actual)


@pytest.mark.parametrize("status", ["FAILED", "ROLLBACK_SUCCEEDED", "ROLLBACK_FAILED", "ROLLBACK_IN_PROGRESS"])
def test_operation_failure_is_never_a_success(monkeypatch, status):
    client = Mock()
    paginator = Mock()
    paginator.paginate.return_value = [{"OperationSummaryList": [{"Id": "id", "Status": status}]}]
    monkeypatch.setattr(publish, "Paginator", Mock(return_value=paginator))
    with pytest.raises(RuntimeError, match=status):
        publish.wait_for_operation(client, "arn", "id")


def test_operation_searches_all_pages(monkeypatch):
    client = Mock()
    paginator = Mock()
    paginator.paginate.return_value = [{"OperationSummaryList": [{"Id": "unrelated", "Status": "SUCCEEDED"}]},
                                      {"OperationSummaryList": [{"Id": "id", "Status": "SUCCEEDED"}]}]
    monkeypatch.setattr(publish, "Paginator", Mock(return_value=paginator))
    publish.wait_for_operation(client, "arn", "id")


def test_operation_timeout_is_bounded(monkeypatch):
    monkeypatch.setattr(publish, "Paginator", Mock())
    with pytest.raises(TimeoutError):
        publish.wait_for_operation(Mock(), "arn", "id", timeout=0)


def test_environment_cannot_silently_target_a_different_database_account():
    environment = {"AURORA_CLUSTER_ARN": "arn:aws:rds:us-east-1:999999999999:cluster:other",
                   "AURORA_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:example-abcdef",
                   "AGENTCORE_RUNTIME_ARN": "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example",
                   "AGENTCORE_WORKFLOW_RUNTIME_ARN": WORKFLOW_ARN}
    with pytest.raises(ValueError, match="AURORA_CLUSTER_ARN"):
        publish.validate_environment(environment, "123456789012", "us-east-1")


def test_a_complete_environment_passes():
    publish.validate_environment(hosted_environment(), "123456789012", "us-east-1")


@pytest.mark.parametrize("arn", [
    None,
    "arn:aws:bedrock-agentcore:us-east-1:999999999999:runtime/other",
    "arn:aws:bedrock-agentcore:eu-west-1:123456789012:runtime/other",
])
def test_the_workflow_runtime_arn_must_be_set_in_the_selected_account(arn):
    environment = hosted_environment()
    environment.pop("AGENTCORE_WORKFLOW_RUNTIME_ARN")
    if arn:
        environment["AGENTCORE_WORKFLOW_RUNTIME_ARN"] = arn
    with pytest.raises(ValueError, match="AGENTCORE_WORKFLOW_RUNTIME_ARN"):
        publish.validate_environment(environment, "123456789012", "us-east-1")


def test_the_backend_login_secret_is_optional_but_must_belong_to_the_account():
    secret = "arn:aws:secretsmanager:us-east-1:123456789012:secret:backend-login-a1B2c3"
    publish.validate_environment(
        hosted_environment(AURORA_BACKEND_SECRET_ARN=secret), "123456789012", "us-east-1")
    other = secret.replace("123456789012", "999999999999")
    with pytest.raises(ValueError, match="AURORA_BACKEND_SECRET_ARN"):
        publish.validate_environment(
            hosted_environment(AURORA_BACKEND_SECRET_ARN=other), "123456789012", "us-east-1")


def control_returning(status, mode="iam", *, gateway_mode=None):
    """A control plane whose hops are in ``mode``; the workflow Runtime reports ``status``."""
    control = Mock()
    runtimes = {
        "example": rs.runtime("MeridianConcierge", mode),
        RUNTIME_ID: {**rs.runtime("MeridianWorkflow", mode), "status": status},
    }
    control.get_agent_runtime.side_effect = lambda agentRuntimeId: runtimes[agentRuntimeId]
    control.get_gateway.return_value = rs.gateway(gateway_mode or mode)
    names = rs.BASE_POLICIES + ([rs.BINDING_POLICY] if mode == "jwt" else [])
    control.list_policies.return_value = {"policies": rs.policies(names)}
    return control


def test_a_ready_workflow_runtime_passes():
    control = control_returning("READY")
    publish.check_workflow_runtime(control, WORKFLOW_ARN)
    control.get_agent_runtime.assert_called_once_with(agentRuntimeId=RUNTIME_ID)


@pytest.mark.parametrize("status", ["CREATING", "FAILED", "UPDATING"])
def test_a_workflow_runtime_that_is_not_ready_refuses_naming_the_status(status):
    with pytest.raises(SystemExit, match=f"MeridianWorkflow is {status}: deploy it"):
        publish.check_workflow_runtime(control_returning(status), WORKFLOW_ARN)


def test_a_workflow_runtime_that_does_not_exist_refuses_as_not_deployed():
    control = Mock()
    control.get_agent_runtime.side_effect = ClientError(
        {"Error": {"Code": "ResourceNotFoundException", "Message": "none"}}, "GetAgentRuntime")
    with pytest.raises(SystemExit, match="MeridianWorkflow is not deployed: deploy it"):
        publish.check_workflow_runtime(control, WORKFLOW_ARN)


def test_the_refusal_names_the_runbook():
    with pytest.raises(SystemExit, match="AGENTCORE_DEPLOY_RUNBOOK.md, then run publish again."):
        publish.check_workflow_runtime(control_returning("FAILED"), WORKFLOW_ARN)


class Commands(list):
    """The commands publish() ran; ``envs`` keeps the extra environment each one was given."""

    def __init__(self):
        super().__init__()
        self.envs = []

    def env_of(self, *command):
        return next(env for ran, env in self.envs if ran == command)


def planned_publish(monkeypatch, tmp_path, control, dotenv=None, outputs=None,
                    service_environment=None, proof=False):
    """Prepare the mocks and files for publish(); return the list that records the commands.

    This does not run publish(). The caller does, and the list fills as it runs. ``dotenv`` is
    what meridian/.env and the process environment hold (default: nothing, so iam mode).
    """
    commands = Commands()
    cdk_out = tmp_path / "infra" / "cdk.out"
    cdk_out.mkdir(parents=True)
    planned = service_environment if service_environment is not None else hosted_environment()
    template = {"Outputs": {"ServiceEnvironment": {"Value": json.dumps(planned)}}}
    monkeypatch.setattr(publish, "release_environment", lambda: dict(dotenv or {}))
    proof_path = tmp_path / "backend-login-proof.json"
    if proof:
        proof_path.write_text(json.dumps(rs.receipt(datetime.now(timezone.utc))))
    monkeypatch.setattr(publish, "PROOF_PATH", proof_path)
    monkeypatch.setattr(settings, "git_head", lambda: rs.SHA)
    (cdk_out / "MeridianWebBackend.template.json").write_text(json.dumps(template))
    session = Mock()
    clients = {"sts": Mock(), "apprunner": Mock(), "cloudformation": Mock(),
               "secretsmanager": Mock(), "bedrock-agentcore-control": control}
    clients["sts"].get_caller_identity.return_value = {"Account": "123456789012"}
    clients["apprunner"].describe_service.return_value = {"Service": {
        **existing(), "ServiceUrl": "x.example.com"}}
    complete = {"Stacks": [{"StackStatus": "UPDATE_COMPLETE"}]}
    clients["cloudformation"].describe_stacks.side_effect = lambda StackName: (
        {"Stacks": [{"StackStatus": "UPDATE_COMPLETE", "Outputs": [
            {"OutputKey": key, "OutputValue": value} for key, value in outputs.items()]}]}
        if StackName == "MeridianIdentity" and outputs is not None else complete)
    clients["secretsmanager"].describe_secret.return_value = {"ARN": "arn:secret"}
    session.client.side_effect = lambda name, **kwargs: clients[name]
    monkeypatch.setattr(publish.boto3, "Session", lambda **kwargs: session)
    monkeypatch.setattr(publish, "container_engine", lambda: "finch")
    monkeypatch.setattr(publish, "run", lambda command, cwd, env=None: (
        commands.append(command), commands.envs.append((tuple(command), env or {}))))
    monkeypatch.setattr(publish, "INFRA", tmp_path / "infra")
    return commands


SERVICE_ARN = "arn:aws:apprunner:us-east-1:123456789012:service/meridian-web/abc123"
ARGS = SimpleNamespace(account="123456789012", region="us-east-1", apply=False,
                       stage=False, tighten=False, service_arn=SERVICE_ARN)


def test_a_dry_run_refuses_an_unready_workflow_runtime_before_the_diff(monkeypatch, tmp_path):
    commands = planned_publish(monkeypatch, tmp_path, control_returning("CREATING"))
    with pytest.raises(SystemExit, match="MeridianWorkflow is CREATING"):
        publish.publish(ARGS)
    assert ["npx", "cdk", "synth", "--quiet"] in commands
    assert ["npx", "cdk", "diff", "--no-change-set"] not in commands


def test_a_dry_run_with_a_ready_workflow_runtime_reaches_the_diff(monkeypatch, tmp_path):
    commands = planned_publish(monkeypatch, tmp_path, control_returning("READY"))
    publish.publish(ARGS)
    assert commands[-1] == ["npx", "cdk", "diff", "--no-change-set"]


# ------------------------------------------------------------- the jwt release

POOL = "us-east-1_AbCdEfGhI"
CLIENT = "exampleclientid123"
IDENTITY_OUTPUTS = {
    "UserPoolId": POOL, "AppClientId": CLIENT,
    "HostedUiDomain": "meridian-x.auth.us-east-1.amazoncognito.com",
    "Issuer": f"https://cognito-idp.us-east-1.amazonaws.com/{POOL}",
}
JWT_DOTENV = {
    "MERIDIAN_AGENTCORE_AUTH": "jwt",
    "MERIDIAN_COGNITO_REGION": "us-east-1",
    "MERIDIAN_COGNITO_USER_POOL_ID": POOL,
    "MERIDIAN_COGNITO_APP_CLIENT_ID": CLIENT,
}


def jwt_service_environment(**overrides):
    return {**hosted_environment(), **rs.jwt_service_variables(), **overrides}


def jwt_publish(monkeypatch, tmp_path, *, control=None, proof=True, service_environment=None,
                **overrides):
    """A publish whose hops, planned service and proof are all ready for the jwt release."""
    return planned_publish(
        monkeypatch, tmp_path, control or control_returning("READY", "jwt"),
        dotenv={**JWT_DOTENV, **overrides}, outputs=IDENTITY_OUTPUTS, proof=proof,
        service_environment=service_environment or jwt_service_environment())


def service_with_shared_token():
    """The live iam service: a shared-token secret reference and the loopback switch."""
    service = existing()
    config = service["SourceConfiguration"]["ImageRepository"]["ImageConfiguration"]
    config["RuntimeEnvironmentSecrets"]["MERIDIAN_API_TOKEN"] = "old-reference"
    config["RuntimeEnvironmentVariables"]["MERIDIAN_ALLOW_INSECURE_LOCALHOST"] = "1"
    return service


def test_the_jwt_service_has_no_shared_token_secret_or_loopback_switch():
    service = existing()
    service["SourceConfiguration"]["ImageRepository"]["ImageConfiguration"][
        "RuntimeEnvironmentSecrets"]["MERIDIAN_API_TOKEN"] = "old-reference"
    service["SourceConfiguration"]["ImageRepository"]["ImageConfiguration"][
        "RuntimeEnvironmentVariables"]["MERIDIAN_ALLOW_INSECURE_LOCALHOST"] = "1"
    before = deepcopy(service)

    definition = publish.service_definition(
        service, "new", {"MERIDIAN_AGENTCORE_AUTH": "jwt"},
        {"AccessRoleArn": "pull", "InstanceRoleArn": "task"}, "token-reference", "jwt")

    config = definition["SourceConfiguration"]["ImageRepository"]["ImageConfiguration"]
    assert config["RuntimeEnvironmentSecrets"] == {"OTHER_SECRET": "other-reference"}
    assert "MERIDIAN_ALLOW_INSECURE_LOCALHOST" not in config["RuntimeEnvironmentVariables"]
    assert config["RuntimeEnvironmentVariables"]["EXISTING_SETTING"] == "preserve"
    assert config["RuntimeEnvironmentVariables"]["MERIDIAN_AGENTCORE_AUTH"] == "jwt"
    assert service == before


def test_the_iam_service_definition_removes_what_only_the_jwt_release_set():
    service = existing()
    variables = service["SourceConfiguration"]["ImageRepository"]["ImageConfiguration"][
        "RuntimeEnvironmentVariables"]
    variables.update({"MERIDIAN_AGENTCORE_AUTH": "jwt", **{
        key: "x" for key in JWT_DOTENV if key.startswith("MERIDIAN_COGNITO")}})

    definition = publish.service_definition(
        service, "new", {"AWS_REGION": "us-east-1"},
        {"AccessRoleArn": "pull", "InstanceRoleArn": "task"}, "token-reference")

    config = definition["SourceConfiguration"]["ImageRepository"]["ImageConfiguration"]
    assert set(config["RuntimeEnvironmentVariables"]) == {"EXISTING_SETTING", "AWS_REGION"}
    assert config["RuntimeEnvironmentSecrets"]["MERIDIAN_API_TOKEN"] == "token-reference"


def test_the_signed_in_build_gets_the_pool_from_the_stack_outputs_and_must_not_ship_ungated():
    assert publish.frontend_build_environment("jwt", IDENTITY_OUTPUTS) == {
        "VITE_API_BASE_URL": "", "VITE_API_ORIGIN": "", "VITE_REQUIRE_SIGN_IN": "1",
        "VITE_COGNITO_DOMAIN": IDENTITY_OUTPUTS["HostedUiDomain"],
        "VITE_COGNITO_CLIENT_ID": CLIENT,
    }


def test_the_iam_build_blanks_any_sign_in_setting_the_shell_exported():
    assert publish.frontend_build_environment("iam", None) == {
        "VITE_API_BASE_URL": "", "VITE_API_ORIGIN": "", "VITE_REQUIRE_SIGN_IN": "",
        "VITE_COGNITO_DOMAIN": "", "VITE_COGNITO_CLIENT_ID": "",
    }


def test_identity_outputs_are_read_from_the_stack_and_a_missing_one_is_named():
    cfn = Mock()
    cfn.describe_stacks.return_value = {"Stacks": [{"Outputs": [
        {"OutputKey": key, "OutputValue": value} for key, value in IDENTITY_OUTPUTS.items()]}]}
    assert publish.identity_outputs(cfn) == IDENTITY_OUTPUTS
    cfn.describe_stacks.assert_called_once_with(StackName="MeridianIdentity")
    cfn.describe_stacks.return_value = {"Stacks": [{"Outputs": [
        {"OutputKey": "UserPoolId", "OutputValue": POOL}]}]}
    with pytest.raises(RuntimeError, match="AppClientId, HostedUiDomain, Issuer"):
        publish.identity_outputs(cfn)


def test_the_stack_and_the_env_file_must_name_the_same_pool_and_client():
    cognito = publish.settings.cognito_settings(JWT_DOTENV)
    publish.check_outputs_match_settings(IDENTITY_OUTPUTS, cognito)
    for key in ("UserPoolId", "AppClientId"):
        with pytest.raises(RuntimeError, match=key):
            publish.check_outputs_match_settings({**IDENTITY_OUTPUTS, key: "other"}, cognito)


def test_a_jwt_plan_builds_the_signed_in_site_and_tells_cdk_the_mode_and_the_host(
        monkeypatch, tmp_path):
    commands = jwt_publish(monkeypatch, tmp_path)

    publish.publish(ARGS)

    build = commands.env_of("npm", "run", "build")
    assert build["VITE_REQUIRE_SIGN_IN"] == "1"
    assert build["VITE_COGNITO_CLIENT_ID"] == CLIENT
    synth = commands.env_of("npx", "cdk", "synth", "--quiet")
    assert synth["MERIDIAN_AGENTCORE_AUTH"] == "jwt"
    assert synth["MERIDIAN_COGNITO_HOSTED_UI_DOMAIN"] == IDENTITY_OUTPUTS["HostedUiDomain"]
    assert synth["MERIDIAN_TIGHTEN_ROLE"] == ""


def test_an_iam_plan_builds_without_sign_in_and_passes_no_pool(monkeypatch, tmp_path):
    commands = planned_publish(monkeypatch, tmp_path, control_returning("READY"))

    publish.publish(ARGS)

    assert commands.env_of("npm", "run", "build")["VITE_REQUIRE_SIGN_IN"] == ""
    synth = commands.env_of("npx", "cdk", "synth", "--quiet")
    assert synth["MERIDIAN_AGENTCORE_AUTH"] == "iam"
    assert synth["MERIDIAN_COGNITO_HOSTED_UI_DOMAIN"] == ""


def test_tighten_sets_the_role_switch_only_in_jwt_mode(monkeypatch, tmp_path):
    commands = jwt_publish(monkeypatch, tmp_path)
    publish.publish(SimpleNamespace(**{**vars(ARGS), "tighten": True}))
    assert commands.env_of("npx", "cdk", "synth", "--quiet")["MERIDIAN_TIGHTEN_ROLE"] == "1"

    commands = planned_publish(monkeypatch, tmp_path / "iam", control_returning("READY"))
    with pytest.raises(ValueError, match="--tighten applies only to the jwt release"):
        publish.publish(SimpleNamespace(**{**vars(ARGS), "tighten": True}))
    assert ["npx", "cdk", "synth", "--quiet"] not in commands


def test_a_jwt_plan_without_the_identity_stack_is_refused_before_the_build(
        monkeypatch, tmp_path):
    commands = planned_publish(
        monkeypatch, tmp_path, control_returning("READY"), dotenv=JWT_DOTENV, outputs={})

    with pytest.raises(RuntimeError, match="MeridianIdentity has no output"):
        publish.publish(ARGS)
    assert ["npm", "run", "build"] not in commands


def test_stage_builds_and_pushes_the_image_and_changes_nothing_else(monkeypatch, tmp_path):
    commands = jwt_publish(monkeypatch, tmp_path)
    monkeypatch.setattr(publish, "LOCAL", tmp_path / ".local")
    (tmp_path / ".local").mkdir()
    (tmp_path / ".local" / "MeridianWebBackend-outputs.json").write_text(
        json.dumps({"MeridianWebBackend": {"ImageUri": "ecr/image:tag"}}))

    publish.publish(SimpleNamespace(**{**vars(ARGS), "stage": True}))

    deploys = [c for c in commands if c[:3] == ["npx", "cdk", "deploy"]]
    assert [c[3] for c in deploys] == ["MeridianWebBackend"]
    assert ["npx", "cdk", "diff", "--no-change-set"] not in commands
    apprunner = publish.boto3.Session().client("apprunner")
    apprunner.update_service.assert_not_called()
    assert not (tmp_path / ".local" / "hosted-release.json").exists()


def test_stage_and_apply_cannot_be_combined():
    with pytest.raises(SystemExit):
        publish.build_parser().parse_args(
            ["--account", "1", "--service-arn", "x", "--stage", "--apply"])
    parsed = publish.build_parser().parse_args(
        ["--account", "1", "--service-arn", "x", "--tighten"])
    assert parsed.tighten is True and parsed.stage is False


# ----------------------------------------------------- the refusing preflight


def test_an_iam_plan_with_every_hop_in_iam_reaches_the_diff(monkeypatch, tmp_path):
    commands = planned_publish(monkeypatch, tmp_path, control_returning("READY"))

    publish.publish(ARGS)

    assert commands[-1] == ["npx", "cdk", "diff", "--no-change-set"]


def test_a_jwt_plan_with_every_hop_ready_reaches_the_diff(monkeypatch, tmp_path):
    commands = jwt_publish(monkeypatch, tmp_path)

    publish.publish(ARGS)

    assert commands[-1] == ["npx", "cdk", "diff", "--no-change-set"]


def test_a_jwt_plan_refuses_while_the_hops_are_still_iam_and_says_which(monkeypatch, tmp_path):
    commands = jwt_publish(monkeypatch, tmp_path, control=control_returning("READY", "iam"))

    with pytest.raises(SystemExit) as refused:
        publish.publish(ARGS)

    text = str(refused.value)
    assert "refusing:" in text and "against the jwt release" in text
    assert "Gateway: authorizer is AWS_IAM, expected CUSTOM_JWT" in text
    assert "Runtime MeridianConcierge: has no JWT authorizer" in text
    assert "Runtime MeridianWorkflow: has no JWT authorizer" in text
    assert "traveler_binding rule is missing" in text
    assert ["npx", "cdk", "diff", "--no-change-set"] not in commands


def test_a_jwt_plan_refuses_when_only_the_gateway_has_not_moved(monkeypatch, tmp_path):
    jwt_publish(monkeypatch, tmp_path, control=control_returning(
        "READY", "jwt", gateway_mode="iam"))

    with pytest.raises(SystemExit) as refused:
        publish.publish(ARGS)

    lines = str(refused.value).splitlines()
    assert any(line.startswith("  Gateway: ") for line in lines)
    assert not any("Runtime" in line for line in lines)


def test_an_iam_plan_refuses_while_the_hops_are_still_jwt(monkeypatch, tmp_path):
    planned_publish(monkeypatch, tmp_path, control_returning("READY", "jwt"))

    with pytest.raises(SystemExit, match="Gateway: authorizer is CUSTOM_JWT, expected AWS_IAM"):
        publish.publish(ARGS)


def test_a_jwt_plan_refuses_without_the_backend_login_proof(monkeypatch, tmp_path):
    jwt_publish(monkeypatch, tmp_path, proof=False)

    with pytest.raises(SystemExit, match="Backend login proof: none recorded"):
        publish.publish(ARGS)


def test_a_jwt_plan_refuses_a_proof_taken_at_another_commit(monkeypatch, tmp_path):
    jwt_publish(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "git_head", lambda: "f" * 40)

    with pytest.raises(SystemExit, match="Backend login proof: its git_sha is not"):
        publish.publish(ARGS)


def test_a_jwt_plan_refuses_a_service_environment_that_still_uses_the_master_login(
        monkeypatch, tmp_path):
    master = "arn:aws:secretsmanager:us-east-1:123456789012:secret:example-abcdef"
    jwt_publish(monkeypatch, tmp_path,
                service_environment=jwt_service_environment(AURORA_SECRET_ARN=master))

    with pytest.raises(SystemExit, match="would not run as the meridian_backend login"):
        publish.publish(ARGS)


def test_a_jwt_plan_refuses_a_service_environment_without_the_pool(monkeypatch, tmp_path):
    environment = jwt_service_environment()
    del environment["MERIDIAN_COGNITO_APP_CLIENT_ID"]
    jwt_publish(monkeypatch, tmp_path, service_environment=environment)

    with pytest.raises(SystemExit, match="MERIDIAN_COGNITO_APP_CLIENT_ID"):
        publish.publish(ARGS)


def test_the_planned_service_never_keeps_the_shared_token_in_the_jwt_release():
    service = service_with_shared_token()
    variables, secrets = publish.planned_service(
        service, jwt_service_environment(), "token-reference", "jwt")

    assert "MERIDIAN_API_TOKEN" not in variables and "MERIDIAN_API_TOKEN" not in secrets
    assert variables["MERIDIAN_AGENTCORE_AUTH"] == "jwt"
    assert secrets == {"OTHER_SECRET": "other-reference"}


def test_the_planned_iam_service_keeps_the_shared_token_reference():
    variables, secrets = publish.planned_service(
        existing(), hosted_environment(), "token-reference", "iam")

    assert secrets["MERIDIAN_API_TOKEN"] == "token-reference"


def test_staging_does_not_need_the_hops_to_have_moved(monkeypatch, tmp_path):
    commands = jwt_publish(monkeypatch, tmp_path, control=control_returning("READY", "iam"),
                           proof=False)
    monkeypatch.setattr(publish, "LOCAL", tmp_path / ".local")
    (tmp_path / ".local").mkdir()
    (tmp_path / ".local" / "MeridianWebBackend-outputs.json").write_text(
        json.dumps({"MeridianWebBackend": {"ImageUri": "ecr/image:tag"}}))

    publish.publish(SimpleNamespace(**{**vars(ARGS), "stage": True}))

    assert [c[3] for c in commands if c[:3] == ["npx", "cdk", "deploy"]] == ["MeridianWebBackend"]


def test_the_identity_output_check_reports_every_mismatch_in_one_error():
    cognito = publish.settings.cognito_settings(JWT_DOTENV)
    wrong = {**IDENTITY_OUTPUTS, "UserPoolId": "other", "AppClientId": "other"}

    with pytest.raises(RuntimeError) as raised:
        publish.check_outputs_match_settings(wrong, cognito)

    assert "UserPoolId" in str(raised.value) and "AppClientId" in str(raised.value)


# ------------------------------------------------ the shell never changes the release


def test_an_iam_plan_and_a_jwt_plan_without_tighten_blank_the_hosted_ui_and_tighten_variables(
        monkeypatch):
    monkeypatch.setenv("MERIDIAN_COGNITO_HOSTED_UI_DOMAIN", "evil.auth.us-east-1.amazoncognito.com")
    monkeypatch.setenv("MERIDIAN_TIGHTEN_ROLE", "1")
    base = {"CDK_DOCKER": "finch"}

    iam = publish.cdk_environment(base, "iam", None, False)
    jwt = publish.cdk_environment(base, "jwt", IDENTITY_OUTPUTS, False)
    tightened = publish.cdk_environment(base, "jwt", IDENTITY_OUTPUTS, True)

    assert iam["MERIDIAN_COGNITO_HOSTED_UI_DOMAIN"] == "" and iam["MERIDIAN_TIGHTEN_ROLE"] == ""
    assert jwt["MERIDIAN_COGNITO_HOSTED_UI_DOMAIN"] == IDENTITY_OUTPUTS["HostedUiDomain"]
    assert jwt["MERIDIAN_TIGHTEN_ROLE"] == ""
    assert tightened["MERIDIAN_TIGHTEN_ROLE"] == "1"
    assert iam["MERIDIAN_AGENTCORE_AUTH"] == "iam" and jwt["MERIDIAN_AGENTCORE_AUTH"] == "jwt"


def test_the_gateway_enforcement_design_is_read_from_the_env_file_not_the_shell(
        monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("MERIDIAN_GATEWAY_ENFORCEMENT=interceptor\nOTHER=kept\n")
    monkeypatch.setattr(publish, "MERIDIAN", tmp_path)
    monkeypatch.setenv("MERIDIAN_GATEWAY_ENFORCEMENT", "cedar")
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")

    assert publish.release_environment()["MERIDIAN_GATEWAY_ENFORCEMENT"] == "interceptor"
    assert publish.release_environment()["MERIDIAN_AGENTCORE_AUTH"] == "jwt"

    env_file.write_text("OTHER=kept\n")
    assert not publish.release_environment().get("MERIDIAN_GATEWAY_ENFORCEMENT")


def test_a_jwt_plan_prints_the_enforcement_design_and_so_does_its_refusal(
        monkeypatch, tmp_path, capsys):
    jwt_publish(monkeypatch, tmp_path)
    publish.publish(ARGS)
    assert "Gateway enforcement design: both" in capsys.readouterr().out

    jwt_publish(monkeypatch, tmp_path / "again", control=control_returning("READY", "iam"),
                MERIDIAN_GATEWAY_ENFORCEMENT="cedar")
    with pytest.raises(SystemExit):
        publish.publish(ARGS)
    assert "Gateway enforcement design: cedar" in capsys.readouterr().out


def test_an_iam_plan_says_the_enforcement_design_does_not_apply(monkeypatch, tmp_path, capsys):
    planned_publish(monkeypatch, tmp_path, control_returning("READY"))
    publish.publish(ARGS)
    assert "Gateway enforcement design: not applicable" in capsys.readouterr().out


# ------------------------------------------------------------------ apply


def apply_publish(monkeypatch, tmp_path, deployed_environment=None):
    """A jwt publish ready to --apply with every AWS call faked; returns (order, client)."""
    jwt_publish(monkeypatch, tmp_path)
    order = []
    deployed = {
        "MeridianWebRoles": {"AccessRoleArn": "pull", "InstanceRoleArn": "task"},
        "MeridianWebBackend": {
            "ImageUri": "ecr/image:tag",
            "ServiceEnvironment": json.dumps(deployed_environment or jwt_service_environment())},
        "MeridianWeb": {"DistributionId": "EXAMPLE"},
    }
    monkeypatch.setattr(publish, "cdk_deploy", lambda stack, env: (order.append(stack),
                                                                    deployed[stack])[1])
    monkeypatch.setattr(publish.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(publish, "wait_for_operation", lambda *args, **kwargs: None)
    monkeypatch.setattr(publish, "LOCAL", tmp_path / ".local")
    client = publish.boto3.Session().client("apprunner")
    live = client.describe_service.return_value["Service"]
    live["SourceConfiguration"] = service_with_shared_token()["SourceConfiguration"]

    def accept(ServiceArn, **definition):
        client.describe_service.return_value = {"Service": {**live, **definition}}
        return {"OperationId": "op"}

    client.update_service.side_effect = accept
    return order, client


def test_apply_deploys_roles_then_backend_then_the_jwt_service_then_the_site(
        monkeypatch, tmp_path):
    order, client = apply_publish(monkeypatch, tmp_path)

    publish.publish(SimpleNamespace(**{**vars(ARGS), "apply": True}))

    assert order == ["MeridianWebRoles", "MeridianWebBackend", "MeridianWeb"]
    sent = client.update_service.call_args.kwargs
    config = sent["SourceConfiguration"]["ImageRepository"]["ImageConfiguration"]
    assert "MERIDIAN_API_TOKEN" not in config["RuntimeEnvironmentVariables"]
    assert "MERIDIAN_API_TOKEN" not in config["RuntimeEnvironmentSecrets"]
    assert "MERIDIAN_ALLOW_INSECURE_LOCALHOST" not in config["RuntimeEnvironmentVariables"]
    assert config["RuntimeEnvironmentVariables"]["MERIDIAN_AGENTCORE_AUTH"] == "jwt"
    receipt = json.loads((tmp_path / ".local" / "hosted-release.json").read_text())
    assert receipt["identityMode"] == "jwt"
    assert receipt["status"] == "deployed_pending_verification"


def test_apply_refuses_a_deployed_service_environment_the_plan_did_not_check(
        monkeypatch, tmp_path):
    master = jwt_service_environment()["AURORA_SECRET_ARN"]
    drifted = jwt_service_environment(AURORA_BACKEND_SECRET_ARN=master + "-other")
    order, client = apply_publish(monkeypatch, tmp_path, deployed_environment=drifted)

    with pytest.raises(SystemExit, match="would not run as the meridian_backend login"):
        publish.publish(SimpleNamespace(**{**vars(ARGS), "apply": True}))

    client.update_service.assert_not_called()
    assert "MeridianWeb" not in order


# ------------------------------------------------------------ AWS failures


def run_main(monkeypatch, capsys):
    monkeypatch.setattr(publish.sys, "argv", [
        "publish.py", "--account", "123456789012", "--service-arn", SERVICE_ARN])
    code = publish.main()
    return code, capsys.readouterr()


def test_a_client_error_reading_the_gateway_refuses_the_publish(monkeypatch, tmp_path, capsys):
    control = control_returning("READY", "jwt")
    control.get_gateway.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "secret detail"}}, "GetGateway")
    commands = jwt_publish(monkeypatch, tmp_path, control=control)

    code, output = run_main(monkeypatch, capsys)

    assert code == 1
    assert "AWS operation failed: AccessDeniedException" in output.err
    assert "secret detail" not in output.err + output.out
    assert ["npx", "cdk", "diff", "--no-change-set"] not in commands


def test_a_connection_failure_refuses_the_publish_with_a_masked_message(
        monkeypatch, tmp_path, capsys):
    control = control_returning("READY", "jwt")
    control.get_gateway.side_effect = EndpointConnectionError(
        endpoint_url="https://private-host.example")
    commands = jwt_publish(monkeypatch, tmp_path, control=control)

    code, output = run_main(monkeypatch, capsys)

    assert code == 1
    assert "AWS connection failed: EndpointConnectionError" in output.err
    assert "private-host.example" not in output.err + output.out
    assert ["npx", "cdk", "diff", "--no-change-set"] not in commands
