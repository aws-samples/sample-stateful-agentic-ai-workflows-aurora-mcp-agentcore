#!/usr/bin/env python3
"""Plan or update an established Meridian deployment without rotating credentials.

Requires the exact account and existing App Runner ARN. Plans build the frontend,
synthesize all three stacks and show their diffs. --apply executes that plan.
Secrets are referenced by ARN for App Runner to resolve; this process never
fetches secret values, changes the edge access store, or deletes services.
New-account provisioning is a separate operation; see docs/OPERATIONS.md.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from botocore.paginate import Paginator

MERIDIAN = Path(__file__).resolve().parents[1]
INFRA = MERIDIAN / "infra"
LOCAL = MERIDIAN / ".local"
STACKS = ("MeridianWebRoles", "MeridianWebBackend", "MeridianWeb")
SECRET_NAME = "meridian/web/api-token"
SERVICE_WAIT_SECONDS = 1200
CONFIG = Config(connect_timeout=10, read_timeout=30, retries={"mode": "standard", "total_max_attempts": 3})


def run(command: list[str], cwd: Path, env: dict | None = None) -> None:
    print("$", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, env={**os.environ, **(env or {})}, check=True)


def validate_target(account: str, region: str, service_arn: str, actual_account: str) -> None:
    if not re.fullmatch(r"\d{12}", account) or account != actual_account:
        raise ValueError("Configured AWS identity does not match --account")
    if not re.fullmatch(rf"arn:aws:apprunner:{re.escape(region)}:{account}:service/meridian-web/[a-f0-9]+", service_arn):
        raise ValueError("--service-arn must name meridian-web in the exact account and region")


def container_engine() -> str:
    requested = os.environ.get("CDK_DOCKER")
    for candidate in ([requested] if requested else ["finch", "docker"]):
        if candidate and shutil.which(candidate):
            subprocess.run([candidate, "info"], capture_output=True, check=True, timeout=30)
            if shutil.disk_usage(MERIDIAN).free < 5 * 1024**3:
                raise RuntimeError("At least 5 GiB of host disk headroom is required before an image build")
            return candidate
    raise RuntimeError("A working Finch or Docker engine is required; see the Finch recovery runbook")


def validate_environment(environment: dict, account: str, region: str) -> None:
    for key, service in (("AURORA_CLUSTER_ARN", "rds"), ("AURORA_SECRET_ARN", "secretsmanager"),
                         ("AGENTCORE_RUNTIME_ARN", "bedrock-agentcore")):
        if not environment.get(key, "").startswith(f"arn:aws:{service}:{region}:{account}:"):
            raise ValueError(f"{key} must belong to the selected account and region")


def service_definition(service: dict, image_uri: str, environment: dict, roles: dict, secret_arn: str) -> dict:
    """Preserve service settings and replace the legacy plaintext token with a reference."""
    current = service["SourceConfiguration"]
    image = current["ImageRepository"]
    config = dict(image.get("ImageConfiguration", {}))
    variables = {**config.get("RuntimeEnvironmentVariables", {}), **environment}
    variables.pop("MERIDIAN_API_TOKEN", None)
    config["RuntimeEnvironmentVariables"] = variables
    config["RuntimeEnvironmentSecrets"] = {**config.get("RuntimeEnvironmentSecrets", {}), "MERIDIAN_API_TOKEN": secret_arn}
    config.setdefault("Port", "8000")
    return {
        "SourceConfiguration": {
            "AuthenticationConfiguration": {"AccessRoleArn": roles["AccessRoleArn"]},
            "AutoDeploymentsEnabled": False,
            "ImageRepository": {"ImageIdentifier": image_uri, "ImageRepositoryType": "ECR", "ImageConfiguration": config},
        },
        "InstanceConfiguration": {**service["InstanceConfiguration"], "InstanceRoleArn": roles["InstanceRoleArn"]},
    }


def wait_for_operation(client, service_arn: str, operation_id: str, timeout: float = SERVICE_WAIT_SECONDS) -> None:
    """RUNNING alone is insufficient: a failed update may have rolled back."""
    deadline = time.monotonic() + timeout
    # App Runner's SDK does not ship a ListOperations paginator model.
    paginator = Paginator(client.list_operations, {
        "input_token": "NextToken", "output_token": "NextToken", "result_key": "OperationSummaryList",
    }, client.meta.service_model.operation_model("ListOperations"))
    while time.monotonic() < deadline:
        for page in paginator.paginate(ServiceArn=service_arn):
            for operation in page["OperationSummaryList"]:
                if operation["Id"] != operation_id:
                    continue
                status = operation["Status"]
                if status == "SUCCEEDED":
                    return
                if status not in {"PENDING", "IN_PROGRESS"}:
                    raise RuntimeError(f"App Runner operation {operation_id} reported {status}; service retained")
        time.sleep(10)
    raise TimeoutError(f"App Runner operation {operation_id} did not complete in {timeout}s; service retained")


def update_service(client, service: dict, definition: dict) -> dict:
    if service["Status"] != "RUNNING":
        raise RuntimeError(f"App Runner is {service['Status']}; refusing to update or delete it")
    if any(service[key] != value for key, value in definition.items()):
        result = client.update_service(ServiceArn=service["ServiceArn"], **definition)
        wait_for_operation(client, service["ServiceArn"], result["OperationId"])
    verified = client.describe_service(ServiceArn=service["ServiceArn"])["Service"]
    if verified["Status"] != "RUNNING" or any(verified[key] != value for key, value in definition.items()):
        raise RuntimeError("App Runner did not retain the requested image/configuration; hosted parity failed")
    return verified


def cdk_deploy(stack: str, env: dict) -> dict:
    outputs = LOCAL / f"{stack}-outputs.json"
    run(["npx", "cdk", "deploy", stack, "--exclusively", "--require-approval", "never",
         "--outputs-file", str(outputs)], INFRA, env)
    return json.loads(outputs.read_text())[stack]


def publish(args) -> None:
    session = boto3.Session(region_name=args.region)
    actual = session.client("sts", config=CONFIG).get_caller_identity()["Account"]
    validate_target(args.account, args.region, args.service_arn, actual)
    client = session.client("apprunner", config=CONFIG)
    service = client.describe_service(ServiceArn=args.service_arn)["Service"]
    if service["Status"] != "RUNNING":
        raise RuntimeError(f"Existing service must be RUNNING, found {service['Status']}")
    cfn = session.client("cloudformation", config=CONFIG)
    for stack in STACKS:
        status = cfn.describe_stacks(StackName=stack)["Stacks"][0]["StackStatus"]
        if status not in {"CREATE_COMPLETE", "UPDATE_COMPLETE", "UPDATE_ROLLBACK_COMPLETE"}:
            raise RuntimeError(f"{stack} is {status}; investigate before publishing")
    secret = session.client("secretsmanager", config=CONFIG).describe_secret(SecretId=SECRET_NAME)
    if secret.get("DeletedDate"):
        raise RuntimeError("Origin token secret is scheduled for deletion")
    engine = container_engine()
    env = {"CDK_DOCKER": engine, "MERIDIAN_WEB_REGION": args.region, "AWS_DEFAULT_REGION": args.region,
           "AWS_REGION": args.region, "CDK_DEFAULT_REGION": args.region, "CDK_DEFAULT_ACCOUNT": args.account,
           "MERIDIAN_BACKEND_HOST": service["ServiceUrl"]}
    run(["npm", "ci"], MERIDIAN / "frontend")
    # Hosted clients use CloudFront's authenticated, same-origin /api route.
    run(["npm", "run", "build"], MERIDIAN / "frontend", {"VITE_API_BASE_URL": "", "VITE_API_ORIGIN": ""})
    run(["npm", "ci"], INFRA)
    run(["npm", "run", "build"], INFRA)
    run(["npx", "cdk", "synth", "--quiet"], INFRA, env)
    template = json.loads((INFRA / "cdk.out" / "MeridianWebBackend.template.json").read_text())
    validate_environment(json.loads(template["Outputs"]["ServiceEnvironment"]["Value"]), args.account, args.region)
    # Template-only diff is read-only: it does not create a change set or publish assets.
    run(["npx", "cdk", "diff", "--no-change-set"], INFRA, env)
    if not args.apply:
        print("Plan complete. No hosted resources changed. Repeat with --apply to execute.")
        return
    LOCAL.mkdir(mode=0o700, exist_ok=True)
    receipt_path = LOCAL / "hosted-release.json"
    receipt = {"startedAt": datetime.now(timezone.utc).isoformat(), "account": args.account,
               "region": args.region, "serviceArn": args.service_arn, "status": "in_progress",
               "previousImage": service["SourceConfiguration"]["ImageRepository"]["ImageIdentifier"]}
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    receipt_path.chmod(0o600)
    roles = cdk_deploy(STACKS[0], env)
    backend = cdk_deploy(STACKS[1], env)
    # IAM changes can propagate while the image builds; a short additional wait
    # is bounded and never treats a failed service update as permission to delete it.
    time.sleep(30)
    latest = client.describe_service(ServiceArn=args.service_arn)["Service"]
    definition = service_definition(latest, backend["ImageUri"], json.loads(backend["ServiceEnvironment"]), roles, secret["ARN"])
    update_service(client, latest, definition)
    site = cdk_deploy(STACKS[2], env)
    receipt.update({"status": "deployed_pending_verification", "image": backend["ImageUri"], "site": site,
                    "finishedAt": datetime.now(timezone.utc).isoformat()})
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"Deployed. Non-secret release receipt: {receipt_path}. Run authenticated hosted validation next.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--service-arn", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        publish(args)
    except ClientError as exc:
        # AWS diagnostics can include request values: expose only the stable error code.
        print(f"AWS operation failed: {exc.response['Error']['Code']}", file=sys.stderr)
        return 1
    except (ValueError, RuntimeError, TimeoutError, subprocess.SubprocessError) as exc:
        print(f"Publish stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
