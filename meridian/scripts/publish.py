#!/usr/bin/env python3
"""Plan or update an established Meridian deployment without rotating credentials.

Requires the exact account and existing App Runner ARN. Plans build the frontend,
synthesize all three stacks and show their diffs. --apply executes that plan.
Secrets are referenced by ARN for App Runner to resolve; this process never
fetches secret values, changes the edge access store, or deletes services.
New-account provisioning is a separate operation; see docs/OPERATIONS.md.

--apply and --stage change AWS, so each also needs --i-understand-this-changes-aws. Exit codes:
0 done (or a plan), 1 a refusal or a failure, 3 a usage error or a missing confirmation flag.
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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from botocore.paginate import Paginator
from dotenv import dotenv_values

MERIDIAN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MERIDIAN))

from backend.agentcore.auth_mode import JWT  # noqa: E402
from scripts.identity_release import preflight, settings  # noqa: E402
from scripts.identity_release.usage import UsageParser  # noqa: E402

INFRA = MERIDIAN / "infra"
LOCAL = MERIDIAN / ".local"
STACKS = ("MeridianWebRoles", "MeridianWebBackend", "MeridianWeb")
SECRET_NAME = "meridian/web/api-token"
IDENTITY_STACK = "MeridianIdentity"
IDENTITY_OUTPUTS = ("UserPoolId", "AppClientId", "HostedUiDomain", "Issuer")
JWT_ONLY_VARIABLES = (
    "MERIDIAN_AGENTCORE_AUTH", "MERIDIAN_COGNITO_REGION", "MERIDIAN_COGNITO_USER_POOL_ID",
    "MERIDIAN_COGNITO_APP_CLIENT_ID",
)
JWT_FORBIDDEN_VARIABLES = ("MERIDIAN_API_TOKEN", "MERIDIAN_ALLOW_INSECURE_LOCALHOST")
PROOF_PATH = settings.PROOF_PATH
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
                         ("AGENTCORE_RUNTIME_ARN", "bedrock-agentcore"),
                         ("AGENTCORE_WORKFLOW_RUNTIME_ARN", "bedrock-agentcore")):
        if not environment.get(key, "").startswith(f"arn:aws:{service}:{region}:{account}:"):
            raise ValueError(f"{key} must belong to the selected account and region")
    backend_secret = environment.get("AURORA_BACKEND_SECRET_ARN")
    if backend_secret and not backend_secret.startswith(
        f"arn:aws:secretsmanager:{region}:{account}:"
    ):
        raise ValueError("AURORA_BACKEND_SECRET_ARN must belong to the selected account and region")


def check_workflow_runtime(control, arn: str) -> None:
    """Refuse to publish unless MeridianWorkflow is READY, so Phase 5 can never ship broken."""
    runtime_id = arn.split("runtime/", 1)[-1]
    try:
        status = control.get_agent_runtime(agentRuntimeId=runtime_id)["status"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        status = "not deployed"
    if status != "READY":
        raise SystemExit(
            f"MeridianWorkflow is {status}: deploy it with the workflow Runtime steps in "
            "docs/AGENTCORE_DEPLOY_RUNBOOK.md, then run publish again."
        )


def release_environment() -> dict:
    """meridian/.env under the process environment, the way the other release scripts read it.

    The Gateway enforcement design is a setting of record: it comes from meridian/.env alone, so
    a variable exported in the operator's shell can neither downgrade nor change what the hops
    are checked against.
    """
    from_file = dotenv_values(MERIDIAN / ".env")
    merged = {**from_file, **os.environ}
    merged[settings.ENFORCEMENT_ENV] = from_file.get(settings.ENFORCEMENT_ENV)
    return merged


def enforcement_line(mode: str, dotenv: dict) -> str:
    """The effective Gateway enforcement design, for the plan and for any refusal."""
    if mode != JWT:
        return "Gateway enforcement design: not applicable (iam release)"
    return f"Gateway enforcement design: {settings.enforcement(dotenv)} (from meridian/.env)"


def identity_outputs(cfn) -> dict:
    """The identity stack's outputs, which the signed-in site is built from.

    Raises:
        RuntimeError: When the stack lacks an output the site or the content security policy needs.
    """
    stack = cfn.describe_stacks(StackName=IDENTITY_STACK)["Stacks"][0]
    outputs = {item["OutputKey"]: item["OutputValue"] for item in stack.get("Outputs", [])}
    missing = [key for key in IDENTITY_OUTPUTS if key not in outputs]
    if missing:
        raise RuntimeError(
            f"{IDENTITY_STACK} has no output {', '.join(missing)}; deploy the identity stack "
            "first (docs/OPERATIONS.md, Sign-in and who is calling)")
    return outputs


def check_outputs_match_settings(outputs: dict, cognito: settings.CognitoSettings) -> None:
    """The site signs in to the stack's client; the backend verifies the .env client. Same one."""
    mismatches = preflight.identity_findings(outputs, cognito)
    if mismatches:
        raise RuntimeError("; ".join(mismatches))


def frontend_build_environment(mode: str, outputs: dict | None) -> dict:
    """The VITE_ settings of the production build, which the process environment sets last.

    The signed-in build must fail rather than ship ungated, hence VITE_REQUIRE_SIGN_IN. An iam
    build blanks all three so a variable exported in the operator's shell cannot gate it.
    """
    base = {"VITE_API_BASE_URL": "", "VITE_API_ORIGIN": ""}
    if mode != JWT:
        return {**base, "VITE_REQUIRE_SIGN_IN": "", "VITE_COGNITO_DOMAIN": "",
                "VITE_COGNITO_CLIENT_ID": ""}
    return {**base, "VITE_REQUIRE_SIGN_IN": "1", "VITE_COGNITO_DOMAIN": outputs["HostedUiDomain"],
            "VITE_COGNITO_CLIENT_ID": outputs["AppClientId"]}


def service_definition(service: dict, image_uri: str, environment: dict, roles: dict,
                       secret_arn: str, mode: str = "iam") -> dict:
    """Preserve service settings; the mode decides the shared token and the sign-in settings.

    In ``iam`` the legacy plaintext token becomes a secret reference and the settings only the jwt
    release sets are removed, so this is also the rollback. In ``jwt`` there is no shared token:
    the secret reference and the loopback switch are removed.
    """
    current = service["SourceConfiguration"]
    image = current["ImageRepository"]
    config = dict(image.get("ImageConfiguration", {}))
    variables = {**config.get("RuntimeEnvironmentVariables", {}), **environment}
    secrets = dict(config.get("RuntimeEnvironmentSecrets", {}))
    variables.pop("MERIDIAN_API_TOKEN", None)
    if mode == JWT:
        for key in JWT_FORBIDDEN_VARIABLES:
            variables.pop(key, None)
        secrets.pop("MERIDIAN_API_TOKEN", None)
    else:
        for key in JWT_ONLY_VARIABLES:
            variables.pop(key, None)
        secrets["MERIDIAN_API_TOKEN"] = secret_arn
    config["RuntimeEnvironmentVariables"] = variables
    config["RuntimeEnvironmentSecrets"] = secrets
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


@dataclass(frozen=True)
class Release:
    """What one publish is about to ship: the mode, the target and the planned service."""

    mode: str
    dotenv: dict
    account: str
    region: str
    service_environment: dict
    secret_arn: str


def planned_service(service: dict, service_environment: dict, secret_arn: str,
                    mode: str) -> tuple[dict, dict]:
    """The plain and secret environment the service would run with after this release."""
    definition = service_definition(
        service, "planned", service_environment,
        {"AccessRoleArn": "", "InstanceRoleArn": ""}, secret_arn, mode)
    config = definition["SourceConfiguration"]["ImageRepository"]["ImageConfiguration"]
    return config["RuntimeEnvironmentVariables"], config["RuntimeEnvironmentSecrets"]


def release_findings(control, release: Release, service: dict, lambda_client) -> list[str]:
    """Everything that must already be true before this release's service and site go out.

    The hops are read back from the control plane (the publish is the last step of the window),
    the service environment is the one this publish would apply, and the jwt release also
    needs the backend login proof and, when the design uses the interceptor, its environment.
    """
    target = preflight.target_for(release.mode, release.dotenv, release.account, release.region)
    gateway_id, runtime_ids = preflight.hop_ids(release.service_environment)
    state = preflight.read_state(control, gateway_id, runtime_ids)
    variables, secrets = planned_service(
        service, release.service_environment, release.secret_arn, release.mode)
    findings = preflight.check_hop_locations(release.service_environment, target)
    findings += preflight.hop_findings(state, target)
    findings += preflight.check_service_environment(variables, secrets, target)
    if release.mode == JWT:
        findings += preflight.interceptor_lambda_findings(lambda_client, target)
        findings += preflight.check_backend_login_proof(PROOF_PATH, target, settings.git_head())
    return findings


def cdk_environment(base: dict, mode: str, outputs: dict | None, tighten: bool) -> dict:
    """What `cdk synth` and `cdk deploy` need to know about the release.

    Every release-mode variable the infra reads is set, empty when unused (the infra treats empty
    as unset), because `run` merges the process environment under these: a value exported in the
    operator's shell must not widen an iam release's CSP or tighten a jwt release.
    """
    jwt = mode == JWT
    return {
        **base, "MERIDIAN_AGENTCORE_AUTH": mode,
        "MERIDIAN_COGNITO_HOSTED_UI_DOMAIN": outputs["HostedUiDomain"] if jwt else "",
        "MERIDIAN_TIGHTEN_ROLE": "1" if jwt and tighten else "",
    }


def inspect_target(session, args) -> tuple:
    """Check the account, the running service, the three stacks and the origin token secret.

    Returns:
        The App Runner client, the service description, the CloudFormation client and the
        origin token secret.
    """
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
    return client, service, cfn, secret


def check_final_definition(definition: dict, release: Release) -> None:
    """Refuse a service definition whose environment the preflight did not see.

    The preflight checks the environment synthesized for the plan; the apply builds the definition
    from the environment the backend stack actually deployed. Both must pass the same check.

    Raises:
        SystemExit: When the final definition has findings.
    """
    config = definition["SourceConfiguration"]["ImageRepository"]["ImageConfiguration"]
    target = preflight.target_for(release.mode, release.dotenv, release.account, release.region)
    preflight.refuse_if_any(preflight.check_service_environment(
        config["RuntimeEnvironmentVariables"], config["RuntimeEnvironmentSecrets"], target),
        release.mode)


def deploy_release(client, args, env: dict, service: dict, release: Release) -> None:
    """Deploy the roles, the image, the service and the site in order, with a release receipt."""
    mode = release.mode
    LOCAL.mkdir(mode=0o700, exist_ok=True)
    receipt_path = LOCAL / "hosted-release.json"
    receipt = {"startedAt": datetime.now(timezone.utc).isoformat(), "account": args.account,
               "region": args.region, "serviceArn": args.service_arn, "status": "in_progress",
               "identityMode": mode,
               "previousImage": service["SourceConfiguration"]["ImageRepository"]["ImageIdentifier"]}
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    receipt_path.chmod(0o600)
    roles = cdk_deploy(STACKS[0], env)
    backend = cdk_deploy(STACKS[1], env)
    # IAM changes can propagate while the image builds; a short additional wait
    # is bounded and never treats a failed service update as permission to delete it.
    time.sleep(30)
    latest = client.describe_service(ServiceArn=args.service_arn)["Service"]
    definition = service_definition(
        latest, backend["ImageUri"], json.loads(backend["ServiceEnvironment"]), roles,
        release.secret_arn, mode)
    check_final_definition(definition, release)
    update_service(client, latest, definition)
    site = cdk_deploy(STACKS[2], env)
    receipt.update({"status": "deployed_pending_verification", "image": backend["ImageUri"], "site": site,
                    "finishedAt": datetime.now(timezone.utc).isoformat()})
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"Deployed. Non-secret release receipt: {receipt_path}. Run authenticated hosted validation next.")


def publish(args) -> None:
    dotenv = release_environment()
    mode = settings.release_mode(dotenv)
    if args.tighten and mode != JWT:
        raise ValueError("--tighten applies only to the jwt release (MERIDIAN_AGENTCORE_AUTH=jwt)")
    session = boto3.Session(region_name=args.region)
    client, service, cfn, secret = inspect_target(session, args)
    outputs = None
    if mode == JWT:
        outputs = identity_outputs(cfn)
        check_outputs_match_settings(outputs, settings.cognito_settings(dotenv))
    engine = container_engine()
    env = cdk_environment(
        {"CDK_DOCKER": engine, "MERIDIAN_WEB_REGION": args.region, "AWS_DEFAULT_REGION": args.region,
         "AWS_REGION": args.region, "CDK_DEFAULT_REGION": args.region,
         "CDK_DEFAULT_ACCOUNT": args.account, "MERIDIAN_BACKEND_HOST": service["ServiceUrl"]},
        mode, outputs, args.tighten)
    run(["npm", "ci"], MERIDIAN / "frontend")
    # Hosted clients use CloudFront's same-origin /api route; a jwt build is the signed-in site.
    run(["npm", "run", "build"], MERIDIAN / "frontend", frontend_build_environment(mode, outputs))
    run(["npm", "ci"], INFRA)
    run(["npm", "run", "build"], INFRA)
    run(["npx", "cdk", "synth", "--quiet"], INFRA, env)
    template = json.loads((INFRA / "cdk.out" / "MeridianWebBackend.template.json").read_text())
    service_environment = json.loads(template["Outputs"]["ServiceEnvironment"]["Value"])
    validate_environment(service_environment, args.account, args.region)
    control = session.client("bedrock-agentcore-control", config=CONFIG)
    check_workflow_runtime(control, service_environment["AGENTCORE_WORKFLOW_RUNTIME_ARN"])
    if args.stage:
        backend = cdk_deploy(STACKS[1], env)
        print(f"Staged the image {backend['ImageUri']} and built the site. The roles, the service "
              "and the site are unchanged.")
        return
    release = Release(mode, dotenv, args.account, args.region, service_environment, secret["ARN"])
    print(enforcement_line(mode, dotenv))
    lambda_client = session.client("lambda", config=CONFIG)
    preflight.refuse_if_any(release_findings(control, release, service, lambda_client), mode)
    # Template-only diff is read-only: it does not create a change set or publish assets.
    run(["npx", "cdk", "diff", "--no-change-set"], INFRA, env)
    if not args.apply:
        print("Plan complete. No hosted resources changed. Repeat with --apply to execute.")
        return
    deploy_release(client, args, env, service, release)


def build_parser() -> argparse.ArgumentParser:
    parser = UsageParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--service-arn", required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--apply", action="store_true")
    action.add_argument("--stage", action="store_true",
                        help="build the site and push the backend image; change nothing live")
    parser.add_argument("--tighten", action="store_true",
                        help="jwt release only: drop the master and shared-token secret grants "
                             "from the App Runner instance role")
    parser.add_argument(settings.CONFIRM_FLAG, action="store_true", dest="confirmed",
                        help="required with --apply or --stage: they change AWS")
    return parser


def require_confirmation(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Exit 3 unless ``--apply`` or ``--stage`` comes with the confirmation flag."""
    if (args.apply or args.stage) and not args.confirmed:
        flag = "--apply" if args.apply else "--stage"
        parser.error(f"{flag} also needs {settings.CONFIRM_FLAG}; it changes AWS")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    require_confirmation(parser, args)
    try:
        publish(args)
    except ClientError as exc:
        # AWS diagnostics can include request values: expose only the stable error code.
        print(f"AWS operation failed: {exc.response['Error']['Code']}", file=sys.stderr)
        return 1
    except BotoCoreError as exc:
        print(f"AWS connection failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    except (ValueError, RuntimeError, TimeoutError, subprocess.SubprocessError) as exc:
        print(f"Publish stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
