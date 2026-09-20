"""Established deployments must fail closed without deleting or rotating access."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from scripts import publish


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
                   "AGENTCORE_RUNTIME_ARN": "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/example"}
    with pytest.raises(ValueError, match="AURORA_CLUSTER_ARN"):
        publish.validate_environment(environment, "123456789012", "us-east-1")
