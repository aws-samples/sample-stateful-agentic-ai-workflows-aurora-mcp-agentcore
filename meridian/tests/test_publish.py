"""Established deployments must fail closed without deleting or rotating access."""
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from scripts import publish


RUNTIME_ID = "meridianv2_MeridianWorkflow-x"
WORKFLOW_ARN = f"arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/{RUNTIME_ID}"


def hosted_environment(**overrides):
    environment = {
        "AURORA_CLUSTER_ARN": "arn:aws:rds:us-east-1:123456789012:cluster:example",
        "AURORA_SECRET_ARN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:example-abcdef",
        "AGENTCORE_RUNTIME_ARN": "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example",
        "AGENTCORE_WORKFLOW_RUNTIME_ARN": WORKFLOW_ARN,
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


def control_returning(status):
    control = Mock()
    control.get_agent_runtime.return_value = {"status": status}
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


def planned_publish(monkeypatch, tmp_path, control):
    """Prepare the mocks and files for publish(); return the list that records the commands.

    This does not run publish(). The caller does, and the list fills as it runs.
    """
    commands = []
    cdk_out = tmp_path / "infra" / "cdk.out"
    cdk_out.mkdir(parents=True)
    template = {"Outputs": {"ServiceEnvironment": {"Value": json.dumps(hosted_environment())}}}
    (cdk_out / "MeridianWebBackend.template.json").write_text(json.dumps(template))
    session = Mock()
    clients = {"sts": Mock(), "apprunner": Mock(), "cloudformation": Mock(),
               "secretsmanager": Mock(), "bedrock-agentcore-control": control}
    clients["sts"].get_caller_identity.return_value = {"Account": "123456789012"}
    clients["apprunner"].describe_service.return_value = {"Service": {
        "Status": "RUNNING", "ServiceUrl": "x.example.com"}}
    complete = {"Stacks": [{"StackStatus": "UPDATE_COMPLETE"}]}
    clients["cloudformation"].describe_stacks.return_value = complete
    clients["secretsmanager"].describe_secret.return_value = {"ARN": "arn:secret"}
    session.client.side_effect = lambda name, **kwargs: clients[name]
    monkeypatch.setattr(publish.boto3, "Session", lambda **kwargs: session)
    monkeypatch.setattr(publish, "container_engine", lambda: "finch")
    monkeypatch.setattr(publish, "run", lambda command, cwd, env=None: commands.append(command))
    monkeypatch.setattr(publish, "INFRA", tmp_path / "infra")
    return commands


SERVICE_ARN = "arn:aws:apprunner:us-east-1:123456789012:service/meridian-web/abc123"
ARGS = SimpleNamespace(account="123456789012", region="us-east-1", apply=False,
                       service_arn=SERVICE_ARN)


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
