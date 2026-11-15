#!/usr/bin/env python3
"""Compare the synthesized App Runner service with the live one, field by field.

Read-only: the only AWS calls are ``apprunner:DescribeService`` and ``ListTagsForResource``.
Run it after ``cdk synth MeridianWebService`` and before the import (docs/OPERATIONS.md). Exit
codes: 0 every compared field matches, 1 a mismatch or a refusal, 2 a usage error.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import boto3

INFRA = Path(__file__).resolve().parents[1] / "infra"
TEMPLATE = INFRA / "cdk.out" / "MeridianWebService.template.json"


MAX_TEMPLATE_AGE_SECONDS = 1800
DEFAULT_SCALING = ("DefaultConfiguration", 1)


def _pairs(items: list[dict] | None) -> dict[str, str]:
    pairs = {item["Name"]: item["Value"] for item in items or []}
    if len(pairs) != len(items or []):
        raise SystemExit("The template lists an environment name twice; App Runner keeps one")
    return pairs


def expected_shape(properties: dict) -> dict:
    """Map the template's CloudFormation properties to the shape DescribeService returns."""
    source = properties["SourceConfiguration"]
    image = source["ImageRepository"]
    config = image["ImageConfiguration"]
    return {
        "ServiceName": properties["ServiceName"],
        "AutoDeploymentsEnabled": source["AutoDeploymentsEnabled"],
        "AccessRoleArn": source["AuthenticationConfiguration"]["AccessRoleArn"],
        "ImageIdentifier": image["ImageIdentifier"],
        "ImageRepositoryType": image["ImageRepositoryType"],
        "Port": config["Port"],
        "StartCommand": config.get("StartCommand"),
        "ObservabilityConfiguration": properties.get("ObservabilityConfiguration"),
        "EncryptionConfiguration": properties.get("EncryptionConfiguration"),
        "Tags": _tags(properties.get("Tags")),
        "RuntimeEnvironmentVariables": _pairs(config.get("RuntimeEnvironmentVariables")),
        "RuntimeEnvironmentSecrets": _pairs(config.get("RuntimeEnvironmentSecrets")),
        "InstanceConfiguration": properties["InstanceConfiguration"],
        "HealthCheckConfiguration": properties["HealthCheckConfiguration"],
        "NetworkConfiguration": properties["NetworkConfiguration"],
    }


def _tags(items: list[dict] | None) -> dict[str, str]:
    pairs = [(i.get("Key", i.get("key")), i.get("Value", i.get("value"))) for i in items or []]
    return dict(pairs)


def live_shape(service: dict, tags: list[dict] | None = None) -> dict:
    """The same fields from a DescribeService response."""
    source = service["SourceConfiguration"]
    image = source["ImageRepository"]
    config = image.get("ImageConfiguration", {})
    health = {k: v for k, v in service["HealthCheckConfiguration"].items() if k != "Path"}
    return {
        "ServiceName": service["ServiceName"],
        "AutoDeploymentsEnabled": source["AutoDeploymentsEnabled"],
        "AccessRoleArn": source["AuthenticationConfiguration"]["AccessRoleArn"],
        "ImageIdentifier": image["ImageIdentifier"],
        "ImageRepositoryType": image["ImageRepositoryType"],
        "Port": config.get("Port"),
        "StartCommand": config.get("StartCommand"),
        "ObservabilityConfiguration": service.get("ObservabilityConfiguration"),
        "EncryptionConfiguration": service.get("EncryptionConfiguration"),
        "Tags": _tags(tags),
        "RuntimeEnvironmentVariables": config.get("RuntimeEnvironmentVariables", {}),
        "RuntimeEnvironmentSecrets": config.get("RuntimeEnvironmentSecrets", {}),
        "InstanceConfiguration": service["InstanceConfiguration"],
        "HealthCheckConfiguration": health,
        "NetworkConfiguration": service["NetworkConfiguration"],
    }


def _differences(name: str, want: object, have: object) -> list[str]:
    if isinstance(want, dict) and isinstance(have, dict):
        return [line for key in sorted(want.keys() | have.keys())
                for line in _differences(f"{name}.{key}", want.get(key), have.get(key))]
    return [] if want == have else [f"{name}: template differs from the live service"]


def scaling_findings(properties: dict, service: dict) -> list[str]:
    """The template names no auto scaling configuration, so the live one must be the default.

    That is an accepted match: App Runner assigns the account's ``DefaultConfiguration`` revision 1
    to a service that names none. A custom configuration is a difference.
    """
    if "AutoScalingConfigurationArn" in properties:
        return ["AutoScalingConfigurationArn: the template must leave the default alone"]
    summary = service.get("AutoScalingConfigurationSummary", {})
    live = (
        summary.get("AutoScalingConfigurationName"),
        summary.get("AutoScalingConfigurationRevision"),
    )
    if live != DEFAULT_SCALING:
        return ["AutoScalingConfiguration: live is not DefaultConfiguration revision 1"]
    return []


def compare(properties: dict, service: dict, tags: list[dict] | None = None) -> list[str]:
    """Field paths that differ. Values are never printed: they hold ARNs and identifiers."""
    want, have = expected_shape(properties), live_shape(service, tags)
    findings = [line for key in want for line in _differences(key, want[key], have[key])]
    findings += scaling_findings(properties, service)
    if service.get("Status") != "RUNNING":
        findings.append(f"Status: service is {service.get('Status')}, not RUNNING")
    return findings


def refuse_stale(path: Path, image_uri: str, properties: dict, now: float | None = None) -> None:
    """Refuse a template that is old or was synthesized for another image."""
    age = (time.time() if now is None else now) - path.stat().st_mtime
    if age > MAX_TEMPLATE_AGE_SECONDS:
        raise SystemExit(f"{path} is {int(age)}s old; run cdk synth again, then this check")
    pinned = properties["SourceConfiguration"]["ImageRepository"]["ImageIdentifier"]
    if pinned != image_uri:
        raise SystemExit("The template image is not the --image-uri you passed; synth again")


def template_properties(path: Path) -> dict:
    resources = json.loads(path.read_text())["Resources"]
    services = [r for r in resources.values() if r["Type"] == "AWS::AppRunner::Service"]
    if len(services) != 1:
        raise SystemExit(f"{path} must hold one AWS::AppRunner::Service, found {len(services)}")
    service = services[0]
    if service.get("DeletionPolicy") != "Retain" or service.get("UpdateReplacePolicy") != "Retain":
        raise SystemExit("The service must have DeletionPolicy and UpdateReplacePolicy Retain")
    return service["Properties"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--service-arn", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--image-uri", required=True, help="the serviceImageUri passed to synth")
    parser.add_argument("--template", type=Path, default=TEMPLATE)
    args = parser.parse_args(argv)
    properties = template_properties(args.template)
    refuse_stale(args.template, args.image_uri, properties)
    client = boto3.client("apprunner", region_name=args.region)
    service = client.describe_service(ServiceArn=args.service_arn)["Service"]
    tags = client.list_tags_for_resource(ResourceArn=args.service_arn)["Tags"]
    findings = compare(properties, service, tags)
    for line in findings:
        print(line)
    print("MATCH" if not findings else f"{len(findings)} difference(s); do not import")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
