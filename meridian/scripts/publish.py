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
# App Runner service creation fails intermittently in this account with no application
# log; the same definition deploys minutes later. The backend stack is retried on its own.
BACKEND_ATTEMPTS = 3
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
    """Write the bearer token to Secrets Manager without ever reading it back; return its ARN."""
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


def deploy_stack(engine: str, region: str, secret_arn: str) -> dict:
    """Deploy the CDK stacks, in order, in the region of Aurora, AgentCore and the token secret.

    The instance role goes first; App Runner cannot deploy a service whose role was
    created in the same CloudFormation run, so after the roles stack is created the
    script waits for IAM to propagate it. The App Runner backend is deployed next and
    retried on its own when App Runner fails, and the site behind CloudFront last.
    """
    run(["npm", "ci"], INFRA)
    run(["npm", "run", "build"], INFRA)
    LOCAL.mkdir(exist_ok=True)
    env = {
        "CDK_DOCKER": engine,
        "MERIDIAN_WEB_REGION": region,
        "MERIDIAN_API_TOKEN_SECRET_ARN": secret_arn,
        "AWS_DEFAULT_REGION": region,
        "AWS_REGION": region,
        "CDK_DEFAULT_REGION": region,
    }
    roles_before = stack_version(region, ROLES_STACK)
    cdk_deploy(ROLES_STACK, env)
    if stack_version(region, ROLES_STACK) != roles_before:
        print(f"Waiting {ROLE_PROPAGATION_SECONDS}s for the App Runner roles to propagate...")
        time.sleep(ROLE_PROPAGATION_SECONDS)
    for attempt in range(1, BACKEND_ATTEMPTS + 1):
        try:
            cdk_deploy(BACKEND_STACK, env)
            break
        except subprocess.CalledProcessError:
            if attempt == BACKEND_ATTEMPTS:
                raise
            print(f"App Runner did not deploy the backend (attempt {attempt} of {BACKEND_ATTEMPTS}); retrying...")
    cdk_deploy(STACK, env)
    outputs = json.loads(OUTPUTS.read_text())
    return {**outputs[BACKEND_STACK], **outputs[STACK]}


def cdk_deploy(stack: str, env: dict) -> None:
    run(
        ["npx", "cdk", "deploy", stack, "--exclusively", "--require-approval", "never",
         "--outputs-file", str(OUTPUTS)],
        INFRA,
        env=env,
    )


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


def redeploy_backend(region: str, service_arn: str) -> None:
    """App Runner reads the secret at deployment, so a new token needs a new deployment."""
    client = boto3.client("apprunner", region_name=region)
    try:
        client.start_deployment(ServiceArn=service_arn)
        print("started an App Runner deployment so the backend picks up the token")
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "")
        if code != "InvalidStateException":
            raise
        print("App Runner is already deploying; the new token applies when it finishes")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-frontend", action="store_true", help="reuse frontend/dist")
    parser.add_argument("--skip-deploy", action="store_true", help="only refresh secrets and the KeyValueStore")
    args = parser.parse_args()

    env = {**dotenv_values(MERIDIAN / ".env"), **os.environ}
    region = env.get("AWS_DEFAULT_REGION", "us-east-1")
    published = load_published()
    token_is_new = not PUBLISHED.exists() or published.get("tokenPublished") != published["token"]
    secret_arn = ensure_secret(region, published["token"])

    if not args.skip_frontend and not args.skip_deploy:
        build_frontend()
    if not args.skip_deploy:
        outputs = deploy_stack(container_engine(), region, secret_arn)
    else:
        outputs = json.loads(OUTPUTS.read_text())[STACK]

    write_access_store(region, outputs["AccessStoreArn"], published)
    if token_is_new and PUBLISHED.exists():
        redeploy_backend(region, outputs["BackendServiceArn"])

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
