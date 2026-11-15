"""The App Runner import pre-check: offline comparison and its read-only guarantee."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

from scripts import check_apprunner_import as check

SCRIPT = Path(check.__file__)
ACCESS = "arn:aws:iam::123456789012:role/access"
INSTANCE = "arn:aws:iam::123456789012:role/instance"

PROPERTIES = {
    "ServiceName": "meridian-web",
    "SourceConfiguration": {
        "AutoDeploymentsEnabled": False,
        "AuthenticationConfiguration": {"AccessRoleArn": ACCESS},
        "ImageRepository": {
            "ImageIdentifier": "123456789012.dkr.ecr.us-east-1.amazonaws.com/a:1",
            "ImageRepositoryType": "ECR",
            "ImageConfiguration": {
                "Port": "8000",
                "RuntimeEnvironmentVariables": [
                    {"Name": "A", "Value": "1"}, {"Name": "B", "Value": "2"}],
            },
        },
    },
    "InstanceConfiguration": {"Cpu": "1024", "Memory": "2048", "InstanceRoleArn": INSTANCE},
    "HealthCheckConfiguration": {
        "Protocol": "TCP", "Interval": 5, "Timeout": 2, "HealthyThreshold": 1,
        "UnhealthyThreshold": 5},
    "NetworkConfiguration": {
        "EgressConfiguration": {"EgressType": "DEFAULT"},
        "IngressConfiguration": {"IsPubliclyAccessible": True}, "IpAddressType": "IPV4"},
}

LIVE = {
    "ServiceName": "meridian-web",
    "Status": "RUNNING",
    "SourceConfiguration": {
        "AutoDeploymentsEnabled": False,
        "AuthenticationConfiguration": {"AccessRoleArn": ACCESS},
        "ImageRepository": {
            "ImageIdentifier": "123456789012.dkr.ecr.us-east-1.amazonaws.com/a:1",
            "ImageRepositoryType": "ECR",
            "ImageConfiguration": {
                "Port": "8000",
                "RuntimeEnvironmentVariables": {"B": "2", "A": "1"},
                "RuntimeEnvironmentSecrets": {},
            },
        },
    },
    "AutoScalingConfigurationSummary": {
        "AutoScalingConfigurationName": "DefaultConfiguration",
        "AutoScalingConfigurationRevision": 1},
    "InstanceConfiguration": {"Cpu": "1024", "Memory": "2048", "InstanceRoleArn": INSTANCE},
    "HealthCheckConfiguration": {
        "Protocol": "TCP", "Path": "/", "Interval": 5, "Timeout": 2, "HealthyThreshold": 1,
        "UnhealthyThreshold": 5},
    "NetworkConfiguration": PROPERTIES["NetworkConfiguration"],
}


def test_matching_service_has_no_findings() -> None:
    assert check.compare(PROPERTIES, LIVE) == []


@pytest.mark.parametrize("mutate, field", [
    (lambda s: s["InstanceConfiguration"].update(Memory="4096"), "InstanceConfiguration.Memory"),
    (lambda s: s["SourceConfiguration"]["ImageRepository"]["ImageConfiguration"][
        "RuntimeEnvironmentVariables"].update(C="3"), "RuntimeEnvironmentVariables.C"),
    (lambda s: s["SourceConfiguration"]["ImageRepository"].update(ImageIdentifier="x"),
     "ImageIdentifier"),
    (lambda s: s["HealthCheckConfiguration"].update(Protocol="HTTP"),
     "HealthCheckConfiguration.Protocol"),
    (lambda s: s.update(Status="OPERATION_IN_PROGRESS"), "Status"),
    (lambda s: s["SourceConfiguration"]["ImageRepository"]["ImageConfiguration"].update(
        StartCommand="x"), "StartCommand"),
    (lambda s: s.update(ObservabilityConfiguration={"ObservabilityEnabled": True}),
     "ObservabilityConfiguration"),
    (lambda s: s.update(EncryptionConfiguration={"KmsKey": "k"}), "EncryptionConfiguration"),
    (lambda s: s["AutoScalingConfigurationSummary"].update(AutoScalingConfigurationRevision=2),
     "AutoScalingConfiguration"),
    (lambda s: s["AutoScalingConfigurationSummary"].update(
        AutoScalingConfigurationName="Custom"), "AutoScalingConfiguration"),
])
def test_each_drifted_field_is_named(mutate, field: str) -> None:
    live = copy.deepcopy(LIVE)
    mutate(live)
    findings = check.compare(PROPERTIES, live)
    assert any(field in line for line in findings), findings


def test_a_live_tag_is_a_difference() -> None:
    findings = check.compare(PROPERTIES, LIVE, [{"Key": "team", "Value": "x"}])
    assert any("Tags" in line for line in findings), findings


def test_a_template_scaling_arn_is_refused() -> None:
    findings = check.compare({**PROPERTIES, "AutoScalingConfigurationArn": "arn"}, LIVE)
    assert any("AutoScalingConfigurationArn" in line for line in findings)


def test_duplicate_environment_names_are_refused() -> None:
    properties = copy.deepcopy(PROPERTIES)
    config = properties["SourceConfiguration"]["ImageRepository"]["ImageConfiguration"]
    config["RuntimeEnvironmentVariables"].append({"Name": "A", "Value": "9"})
    with pytest.raises(SystemExit):
        check.compare(properties, LIVE)


def test_a_stale_template_is_refused(tmp_path: Path) -> None:
    template = tmp_path / "t.json"
    template.write_text("{}")
    image = PROPERTIES["SourceConfiguration"]["ImageRepository"]["ImageIdentifier"]
    mtime = template.stat().st_mtime
    check.refuse_stale(template, image, PROPERTIES, now=mtime + 60)
    with pytest.raises(SystemExit):
        check.refuse_stale(template, image, PROPERTIES, now=mtime + 3600)
    with pytest.raises(SystemExit):
        check.refuse_stale(template, "another-image", PROPERTIES, now=mtime + 60)


def test_findings_never_print_values() -> None:
    live = copy.deepcopy(LIVE)
    live["InstanceConfiguration"]["InstanceRoleArn"] = "arn:aws:iam::999999999999:role/other"
    text = "\n".join(check.compare(PROPERTIES, live))
    assert "999999999999" not in text and "123456789012" not in text


def test_template_without_retain_is_refused(tmp_path: Path) -> None:
    template = tmp_path / "t.json"
    template.write_text(json.dumps({"Resources": {"S": {
        "Type": "AWS::AppRunner::Service", "Properties": PROPERTIES}}}))
    with pytest.raises(SystemExit):
        check.template_properties(template)


def test_script_only_describes() -> None:
    calls = set(re.findall(r"client\.(\w+)\(", SCRIPT.read_text()))
    assert calls == {"describe_service", "list_tags_for_resource"}
