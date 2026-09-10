#!/usr/bin/env python3
"""Publish Meridian to a password-protected CloudFront URL.

The CDK app in infra/ owns every resource: a private S3 bucket for the Vite
build, the FastAPI backend as a container on App Runner, a CloudFront
distribution in front of both, a viewer function that enforces basic auth and
injects the backend bearer token, and the KeyValueStore that holds those two
values. This script does the parts CDK should not: it mints the credentials,
writes the bearer token to Secrets Manager (write only, never read), builds the
frontend, deploys the stack with Finch or Docker, writes the KeyValueStore, and
records the URL and credentials in meridian/.local/published.json (gitignored).

Re-running reuses the credentials on disk, rebuilds, and redeploys.

Usage:
    cd meridian
    python scripts/publish.py [--skip-frontend] [--skip-deploy]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import dotenv_values

MERIDIAN = Path(__file__).resolve().parents[1]
INFRA = MERIDIAN / "infra"
FRONTEND = MERIDIAN / "frontend"
LOCAL = MERIDIAN / ".local"
PUBLISHED = LOCAL / "published.json"
OUTPUTS = LOCAL / "cdk-outputs.json"
STACK = "MeridianWeb"
ROLES_STACK = "MeridianWebRoles"
BACKEND_STACK = "MeridianWebBackend"
# IAM propagation. App Runner fails to deploy a service whose roles were created moments earlier.
ROLE_PROPAGATION_SECONDS = 90
SERVICE_NAME = "meridian-web"
# App Runner service creation has failed intermittently here with no application log;
# a failed creation is deleted and tried again.
SERVICE_ATTEMPTS = 3
SERVICE_WAIT_SECONDS = 600
SECRET_NAME = "meridian/web/api-token"
USER = "meridian"


def run(command: list[str], cwd: Path, env: dict | None = None) -> None:
    print("$", " ".join(command), f"  (in {cwd.relative_to(MERIDIAN.parent)})", flush=True)
    subprocess.run(command, cwd=cwd, env={**os.environ, **(env or {})}, check=True)


def container_engine() -> str:
    for candidate in ("docker", "finch"):
        if shutil.which(candidate):
            return candidate
    raise SystemExit("Neither docker nor finch is on PATH; install Finch and run `finch vm start`.")


def load_published() -> dict:
    if PUBLISHED.exists():
        return json.loads(PUBLISHED.read_text())
    return {
        "user": USER,
        "password": secrets.token_urlsafe(18),
        "token": secrets.token_urlsafe(32),
    }


def ensure_secret(region: str, token: str) -> str:
    """Keep the bearer token on record in Secrets Manager, never reading it back; return its ARN."""
    client = boto3.client("secretsmanager", region_name=region)
    try:
        arn = client.describe_secret(SecretId=SECRET_NAME)["ARN"]
        client.put_secret_value(SecretId=SECRET_NAME, SecretString=token)
        print(f"updated secret {SECRET_NAME}")
    except client.exceptions.ResourceNotFoundException:
        arn = client.create_secret(
            Name=SECRET_NAME,
            Description="Meridian backend bearer token injected by the CloudFront viewer function",
            SecretString=token,
        )["ARN"]
        print(f"created secret {SECRET_NAME}")
    return arn


def build_frontend() -> None:
    run(["npm", "ci"], FRONTEND)
    run(["npm", "run", "build"], FRONTEND)


def stack_version(region: str, name: str) -> str | None:
    """The stack's last change time, or None when it does not exist or is a failed creation."""
    try:
        stacks = boto3.client("cloudformation", region_name=region).describe_stacks(StackName=name)
    except ClientError as exc:
        if "does not exist" in str(exc):
            return None
        raise
    stack = stacks["Stacks"][0]
    if stack["StackStatus"] in {"ROLLBACK_COMPLETE", "DELETE_IN_PROGRESS", "DELETE_COMPLETE"}:
        return None
    return str(stack.get("LastUpdatedTime") or stack["CreationTime"])


def cdk_deploy(stack: str, env: dict) -> dict:
    run(
        ["npx", "cdk", "deploy", stack, "--exclusively", "--require-approval", "never",
         "--outputs-file", str(OUTPUTS)],
        INFRA,
        env=env,
    )
    return json.loads(OUTPUTS.read_text())[stack]


def find_service(client) -> dict | None:
    for summary in client.list_services()["ServiceSummaryList"]:
        if summary["ServiceName"] == SERVICE_NAME:
            return client.describe_service(ServiceArn=summary["ServiceArn"])["Service"]
    return None


def wait_for_service(client, service_arn: str) -> str:
    """Poll until the service leaves OPERATION_IN_PROGRESS; return its status."""
    for _ in range(SERVICE_WAIT_SECONDS // 15):
        status = client.describe_service(ServiceArn=service_arn)["Service"]["Status"]
        if status != "OPERATION_IN_PROGRESS":
            return status
        time.sleep(15)
    return "OPERATION_IN_PROGRESS"


def wait_for_deletion(client, service_arn: str) -> None:
    """Poll until the service is gone; App Runner reports DELETED before it stops describing it."""
    for _ in range(SERVICE_WAIT_SECONDS // 15):
        try:
            status = client.describe_service(ServiceArn=service_arn)["Service"]["Status"]
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
                return
            raise
        if status == "DELETED":
            return
        time.sleep(15)


def service_definition(image_uri: str, environment: dict, roles: dict, token: str) -> dict:
    """The App Runner service, with no optional setting.

    App Runner in us-east-1 refused to deploy any service whose CreateService
    call carried a custom auto scaling configuration, a health check interval,
    an explicit egress configuration, a tag list, or a Secrets Manager reference
    for the token, and CloudFormation always sends a tag list, so the service is
    created here with the SDK and the token travels as a runtime environment
    variable. It guards only the App Runner origin behind CloudFront, and the
    same value already sits in the CloudFront KeyValueStore.
    """
    return {
        "SourceConfiguration": {
            "AuthenticationConfiguration": {"AccessRoleArn": roles["AccessRoleArn"]},
            "AutoDeploymentsEnabled": False,
            "ImageRepository": {
                "ImageIdentifier": image_uri,
                "ImageRepositoryType": "ECR",
                "ImageConfiguration": {
                    "Port": "8000",
                    "RuntimeEnvironmentVariables": {**environment, "MERIDIAN_API_TOKEN": token},
                },
            },
        },
        "InstanceConfiguration": {"Cpu": "1024", "Memory": "2048", "InstanceRoleArn": roles["InstanceRoleArn"]},
    }


def ensure_service(region: str, image_uri: str, environment: dict, roles: dict, token: str) -> dict:
    """Create or update the App Runner service and wait until it runs.

    A failed creation is deleted and retried; App Runner service creation has
    failed intermittently here with no application log.
    """
    client = boto3.client("apprunner", region_name=region)
    definition = service_definition(image_uri, environment, roles, token)
    for attempt in range(1, SERVICE_ATTEMPTS + 1):
        service = find_service(client)
        if service and service["Status"] == "OPERATION_IN_PROGRESS":
            print("App Runner is still working on the backend; waiting...")
            service["Status"] = wait_for_service(client, service["ServiceArn"])
        if service and service["Status"] in {"CREATE_FAILED", "DELETE_FAILED"}:
            print(f"removing the backend service left in {service['Status']}")
            client.delete_service(ServiceArn=service["ServiceArn"])
            wait_for_deletion(client, service["ServiceArn"])
            service = None
        if service is None:
            print(f"creating the App Runner service (attempt {attempt} of {SERVICE_ATTEMPTS})")
            service = client.create_service(ServiceName=SERVICE_NAME, **definition)["Service"]
        else:
            current = service["SourceConfiguration"]["ImageRepository"]
            same = (
                current["ImageIdentifier"] == image_uri
                and current["ImageConfiguration"].get("RuntimeEnvironmentVariables") == definition["SourceConfiguration"]["ImageRepository"]["ImageConfiguration"]["RuntimeEnvironmentVariables"]
            )
            if same and service["Status"] == "RUNNING":
                print("the backend already runs this image with this configuration")
                return service
            print("updating the App Runner service with the new image or configuration")
            service = client.update_service(ServiceArn=service["ServiceArn"], **definition)["Service"]
        status = wait_for_service(client, service["ServiceArn"])
        if status == "RUNNING":
            return client.describe_service(ServiceArn=service["ServiceArn"])["Service"]
        print(f"App Runner reported {status} for the backend")
    raise SystemExit("App Runner did not bring the backend up; see the service event log in CloudWatch")


def deploy_stack(engine: str, region: str, token: str) -> dict:
    """Deploy the roles, the image, the App Runner service and the site, in that order.

    The roles go first; App Runner cannot deploy a service whose roles were created
    moments earlier, so after the roles stack is created or changed the script
    waits for IAM to propagate them. The image stack pushes the backend image, the
    service is created with the SDK, and the site stack needs the service host.
    """
    run(["npm", "ci"], INFRA)
    run(["npm", "run", "build"], INFRA)
    LOCAL.mkdir(exist_ok=True)
    env = {
        "CDK_DOCKER": engine,
        "MERIDIAN_WEB_REGION": region,
        "AWS_DEFAULT_REGION": region,
        "AWS_REGION": region,
        "CDK_DEFAULT_REGION": region,
    }
    roles_before = stack_version(region, ROLES_STACK)
    roles = cdk_deploy(ROLES_STACK, env)
    if stack_version(region, ROLES_STACK) != roles_before:
        print(f"Waiting {ROLE_PROPAGATION_SECONDS}s for the App Runner roles to propagate...")
        time.sleep(ROLE_PROPAGATION_SECONDS)
    backend = cdk_deploy(BACKEND_STACK, env)
    service = ensure_service(
        region, backend["ImageUri"], json.loads(backend["ServiceEnvironment"]), roles, token
    )
    site = cdk_deploy(STACK, {**env, "MERIDIAN_BACKEND_HOST": service["ServiceUrl"]})
    return {
        **site,
        "BackendUrl": f"https://{service['ServiceUrl']}",
        "BackendServiceArn": service["ServiceArn"],
    }


def write_access_store(region: str, kvs_arn: str, published: dict) -> None:
    """Put the basic credential and the bearer token into the KeyValueStore via the AWS CLI.

    The KeyValueStore data plane needs SigV4A signing, which the CLI carries and the
    backend virtualenv does not.
    """
    basic = base64.b64encode(f"{published['user']}:{published['password']}".encode()).decode()
    described = subprocess.run(
        ["aws", "cloudfront-keyvaluestore", "describe-key-value-store", "--kvs-arn", kvs_arn,
         "--region", region, "--output", "json"],
        capture_output=True, text=True, check=True,
    )
    etag = json.loads(described.stdout)["ETag"]
    payload = {
        "KvsARN": kvs_arn,
        "IfMatch": etag,
        "Puts": [{"Key": "basic", "Value": basic}, {"Key": "token", "Value": published["token"]}],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", dir=LOCAL, delete=False) as handle:
        json.dump(payload, handle)
        temp = Path(handle.name)
    try:
        subprocess.run(
            ["aws", "cloudfront-keyvaluestore", "update-keys", "--cli-input-json", f"file://{temp}",
             "--region", region, "--output", "json"],
            capture_output=True, text=True, check=True,
        )
    finally:
        temp.unlink(missing_ok=True)
    print("wrote basic credential and bearer token to the KeyValueStore")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-frontend", action="store_true", help="reuse frontend/dist")
    parser.add_argument("--skip-deploy", action="store_true", help="only refresh secrets and the KeyValueStore")
    args = parser.parse_args()

    env = {**dotenv_values(MERIDIAN / ".env"), **os.environ}
    region = env.get("AWS_DEFAULT_REGION", "us-east-1")
    published = load_published()
    ensure_secret(region, published["token"])

    if not args.skip_frontend and not args.skip_deploy:
        build_frontend()
    if not args.skip_deploy:
        outputs = deploy_stack(container_engine(), region, published["token"])
    else:
        outputs = {
            **json.loads(OUTPUTS.read_text())[STACK],
            "BackendUrl": published["backendUrl"],
            "BackendServiceArn": published["backendServiceArn"],
        }

    write_access_store(region, outputs["AccessStoreArn"], published)

    published.update({
        "url": outputs["SiteUrl"],
        "backendUrl": outputs["BackendUrl"],
        "distributionId": outputs["DistributionId"],
        "accessStoreArn": outputs["AccessStoreArn"],
        "backendServiceArn": outputs["BackendServiceArn"],
        "tokenPublished": published["token"],
    })
    LOCAL.mkdir(exist_ok=True)
    PUBLISHED.write_text(json.dumps(published, indent=2) + "\n")
    PUBLISHED.chmod(0o600)
    print(f"\nMeridian is published at {published['url']}")
    print(f"Basic auth user '{USER}'; the password and the backend token are in {PUBLISHED} (never commit it).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
