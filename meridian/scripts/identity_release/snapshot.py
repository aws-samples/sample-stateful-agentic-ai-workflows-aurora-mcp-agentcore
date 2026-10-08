"""Save the configuration the release replaces, before the window opens, without any secret.

The snapshot holds what a rollback has to put back: the Gateway (every field ``update_gateway``
takes), both Runtimes, the App Runner service definition, the hosted site's identity (the viewer
function, the response headers policy and the function associations), the roles stack's
template hash, the holds and semantic-search Lambdas' environments, the SSM parameter that
names the login secret, and the active Cedar rules. It only reads.

It holds names, ARNs and settings, never a secret value. A plain value whose name looks like a
credential is replaced with ``<redacted>``, a token-shaped or access-key-shaped string with
``<token>``, and every such path is listed under ``redacted``. A hop whose restore view contains
a placeholder is not restored automatically (the rollback says so), because a placeholder
written back would break it. The file is written atomically, mode 0600 in a 0700 folder, and
carries a SHA-256 over its content so a changed or truncated copy is refused. The hash detects
accidents and edits; it is not a signature.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.agentcore.auth_mode import IAM, JWT
from scripts.gateway_harness.private_files import private_dir, write_private
from scripts.gateway_harness.verdicts import JWT_SHAPE
from scripts.identity_release import gateway_release, lambda_release, preflight, settings
from scripts.provision_service_logins import require_account

SCHEMA = "meridian-release-snapshot/1"
EDGE_FUNCTION = "meridian-web-viewer"
ROLES_STACK = "MeridianWebRoles"
HOSTED_RELEASE_PATH = settings.MERIDIAN_DIR / ".local" / "hosted-release.json"
SENSITIVE_NAME = re.compile(
    r"TOKEN|PASSWORD|SECRET|CREDENTIAL|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY|(^|[_-])KEY($|[_-])",
    re.IGNORECASE)
NOT_SENSITIVE = re.compile(r"(^|[_-])TOKEN[_-]USE$|(^|[_-])MAX[_-]TOKENS$", re.IGNORECASE)
ACCESS_KEY_SHAPE = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
DISTRIBUTION_ID = re.compile(r"^E[A-Z0-9]{8,20}$")
REDACTED = "<redacted>"
MASKED = "<token>"
STAMP = "%Y%m%dT%H%M%SZ"
NAME = re.compile(r"snapshot-\d{8}T\d{6}Z\.json")
SECTIONS = ("gateway", "runtimes", "service", "site", "roles", "lambdas", "policies")
MODES = (IAM, JWT)
TOP_LEVEL_TYPES = (
    ("takenAt", str), ("commit", str), ("account", str), ("region", str), ("gateway", dict),
    ("runtimes", dict), ("service", dict), ("site", dict), ("roles", dict), ("lambdas", dict),
    ("policies", dict), ("baselineFindings", list), ("redacted", list))
RECEIPT_FIELDS = ("status", "identityMode", "previousImage", "image")
SERVICE_KEPT = ("ServiceName", "ServiceArn", "SourceConfiguration", "InstanceConfiguration",
                "HealthCheckConfiguration", "NetworkConfiguration", "ObservabilityConfiguration",
                "AutoScalingConfigurationSummary")
SERVICE_UPDATABLE = ("SourceConfiguration", "InstanceConfiguration", "HealthCheckConfiguration",
                     "NetworkConfiguration", "ObservabilityConfiguration")
GATEWAY_VIEW = gateway_release.KEPT_FIELDS + gateway_release.CHANGED_FIELDS
RUNTIME_FIELDS = (
    "agentRuntimeArtifact", "roleArn", "networkConfiguration", "description",
    "authorizerConfiguration", "requestHeaderConfiguration", "protocolConfiguration",
    "lifecycleConfiguration", "metadataConfiguration", "environmentVariables",
    "filesystemConfigurations", "capacityProviderConfiguration",
)
RUNTIME_READ_ONLY = (
    "agentRuntimeArn", "agentRuntimeName", "agentRuntimeId", "agentRuntimeVersion", "createdAt",
    "lastUpdatedAt", "status", "failureReason", "workloadIdentityDetails",
)


class SnapshotError(settings.ReleaseConfigError):
    """A snapshot cannot be taken, or a saved one cannot be read or trusted."""


@dataclass(frozen=True)
class Clients:
    """The AWS clients the snapshot and the rollback use, built after the account guard."""

    control: Any
    apprunner: Any
    cloudfront: Any
    cfn: Any
    ssm: Any
    lam: Any


@dataclass(frozen=True)
class Where:
    """The deployment: account, Region, hop ids, the service and the settings."""

    account: str
    region: str
    gateway_id: str
    runtime_ids: Mapping[str, str]
    service_arn: str
    env: Mapping[str, str | None]


# ------------------------------------------------------------------ what is saved


def present(value: Any) -> bool:
    """False for ``None`` and empty containers: the API treats them as not set."""
    return value is not None and value != [] and value != {}


def gateway_view(described: Mapping[str, Any]) -> dict[str, Any]:
    """The fields ``update_gateway`` takes, as the Gateway has them."""
    return {key: deepcopy(described[key]) for key in GATEWAY_VIEW if present(described.get(key))}


def runtime_view(described: Mapping[str, Any]) -> dict[str, Any]:
    """The fields ``update_agent_runtime`` takes, as the Runtime has them."""
    return {key: deepcopy(described[key]) for key in RUNTIME_FIELDS if present(described.get(key))}


def service_view(service: Mapping[str, Any]) -> dict[str, Any]:
    """The fields ``update_service`` takes, as the service has them."""
    view = {key: deepcopy(service[key]) for key in SERVICE_UPDATABLE if present(service.get(key))}
    scaling = (service.get("AutoScalingConfigurationSummary") or {}).get(
        "AutoScalingConfigurationArn")
    if scaling:
        view["AutoScalingConfigurationArn"] = scaling
    return view


def _json_safe(value: Any) -> Any:
    def plain(item: Any) -> str:
        return item.isoformat() if isinstance(item, datetime) else str(item)

    return json.loads(json.dumps(value, default=plain))


def _clean_text(text: str) -> str:
    return ACCESS_KEY_SHAPE.sub(MASKED, JWT_SHAPE.sub(MASKED, text))


def _clean(node: Any, path: str, found: list[str]) -> Any:
    """A copy of ``node`` with credential-looking values redacted; their paths go in ``found``."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            here = f"{path}.{key}" if path else key
            secret = isinstance(value, str) and value and SENSITIVE_NAME.search(key) \
                and not NOT_SENSITIVE.search(key) and not value.startswith("arn:")
            if secret:
                out[key] = REDACTED
                found.append(here)
            else:
                out[key] = _clean(value, here, found)
        return out
    if isinstance(node, list):
        return [_clean(item, f"{path}[{index}]", found) for index, item in enumerate(node)]
    if isinstance(node, str) and _clean_text(node) != node:
        found.append(path)
        return _clean_text(node)
    return node


def redact(node: Any, root: str = "") -> tuple[Any, list[str]]:
    """``node`` with credential-looking values redacted, and the paths that were."""
    found: list[str] = []
    return _clean(node, root, found), found


def placeholders(node: Any, path: str = "") -> list[str]:
    """Paths in ``node`` with a redaction or token placeholder anywhere inside a string."""
    if isinstance(node, dict):
        return [p for key, value in node.items()
                for p in placeholders(value, f"{path}.{key}" if path else key)]
    if isinstance(node, list):
        return [p for index, item in enumerate(node)
                for p in placeholders(item, f"{path}[{index}]")]
    marked = isinstance(node, str) and (REDACTED in node or MASKED in node)
    return [path] if marked else []


def redacted_under(saved: Mapping[str, Any], prefix: str) -> list[str]:
    """The paths the snapshot redacted or masked inside the hop at ``prefix``."""
    return [path for path in saved.get("redacted") or []
            if path == prefix or path.startswith((f"{prefix}.", f"{prefix}["))]


def utc_stamp(moment: datetime) -> str:
    """``moment`` in UTC as the stamp used in file names and the restart marker.

    Raises:
        SnapshotError: When ``moment`` has no time zone.
    """
    return as_utc(moment).strftime(STAMP)


def as_utc(moment: datetime) -> datetime:
    """``moment`` converted to UTC.

    Raises:
        SnapshotError: When ``moment`` has no time zone (its UTC time would be a guess).
    """
    if moment.tzinfo is None:
        raise SnapshotError("a time without a time zone was given; the stamps are UTC and need "
                            "an explicit zone")
    return moment.astimezone(timezone.utc)


# ---------------------------------------------------------------------- taking


def _gateway_section(state: preflight.HopState, where: Where,
                     target: preflight.Target) -> dict[str, Any]:
    problems = gateway_release.snapshot_problems(state.gateway, where.gateway_id, target)
    if problems:
        raise SnapshotError("the Gateway cannot be saved in full:\n  " + "\n  ".join(problems))
    return {key: value for key, value in state.gateway.items() if key != "ResponseMetadata"}


def _runtime_section(name: str, described: Any) -> dict[str, Any]:
    if not isinstance(described, Mapping):
        raise SnapshotError(f"Runtime {name}: the description is unreadable")
    known = set(RUNTIME_FIELDS) | set(RUNTIME_READ_ONLY)
    unknown = sorted(key for key, value in described.items()
                     if key not in known and key != "ResponseMetadata" and value)
    if unknown:
        raise SnapshotError(f"Runtime {name}: has fields the restore would not resend: "
                            + ", ".join(unknown))
    if described.get("status") != "READY":
        raise SnapshotError(f"Runtime {name}: status is {described.get('status')}; wait for "
                            "READY before saving it")
    return {key: value for key, value in described.items() if key != "ResponseMetadata"}


def _service_section(apprunner: Any, service_arn: str) -> dict[str, Any]:
    described = apprunner.describe_service(ServiceArn=service_arn)["Service"]
    if described.get("Status") != "RUNNING":
        raise SnapshotError(f"the service is {described.get('Status')}, not RUNNING; wait for "
                            "it before saving it")
    if preflight.image_environment(described) is None:
        raise SnapshotError("the service is not image-based, so its environment cannot be saved")
    return {key: described[key] for key in SERVICE_KEPT if key in described}


def _receipt(path: Path) -> dict[str, Any]:
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SnapshotError(f"cannot read {path.name}; the hosted release writes it, and the "
                            "snapshot needs the site's DistributionId from it") from None
    site = receipt.get("site") if isinstance(receipt, dict) else None
    distribution = site.get("DistributionId") if isinstance(site, dict) else None
    if not isinstance(distribution, str) or not DISTRIBUTION_ID.fullmatch(distribution):
        raise SnapshotError(f"{path.name} has no usable site.DistributionId")
    kept = {key: receipt[key] for key in RECEIPT_FIELDS if key in receipt}
    return {"distributionId": distribution, "hostedRelease": kept}


def behaviors_of(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    entries = [("default", config.get("DefaultCacheBehavior") or {})]
    entries += [(item.get("PathPattern"), item)
                for item in (config.get("CacheBehaviors") or {}).get("Items") or []]
    return [{"pathPattern": pattern,
             "responseHeadersPolicyId": behavior.get("ResponseHeadersPolicyId"),
             "functionAssociations": [
                 {"eventType": item.get("EventType"), "functionArn": item.get("FunctionARN")}
                 for item in (behavior.get("FunctionAssociations") or {}).get("Items") or []]}
            for pattern, behavior in entries]


def read_viewer(cloudfront: Any, stage: str) -> dict[str, Any]:
    """The viewer function's code, configuration and ETag at ``stage``."""
    described = cloudfront.describe_function(Name=EDGE_FUNCTION, Stage=stage)
    code = cloudfront.get_function(Name=EDGE_FUNCTION, Stage=stage)
    return {"name": EDGE_FUNCTION, "etag": described["ETag"],
            "config": described["FunctionSummary"]["FunctionConfig"],
            "code": code["FunctionCode"].read().decode("utf-8")}


def _site_section(cloudfront: Any, receipt_path: Path) -> dict[str, Any]:
    site = _receipt(receipt_path)
    config = cloudfront.get_distribution_config(Id=site["distributionId"])["DistributionConfig"]
    behaviors = behaviors_of(config)
    policy_id = behaviors[0]["responseHeadersPolicyId"]
    if not policy_id:
        raise SnapshotError("the distribution's default behavior has no response headers policy")
    policy = cloudfront.get_response_headers_policy(Id=policy_id)
    policy_config = policy["ResponseHeadersPolicy"]["ResponseHeadersPolicyConfig"]
    return {**site, "viewerFunction": read_viewer(cloudfront, "LIVE"), "behaviors": behaviors,
            "responseHeadersPolicy": {"id": policy_id, "name": policy_config.get("Name"),
                                      "etag": policy["ETag"], "config": policy_config}}


def template_hash(cfn: Any) -> str:
    """SHA-256 of the roles stack's original template."""
    body = cfn.get_template(StackName=ROLES_STACK, TemplateStage="Original")["TemplateBody"]
    text = body if isinstance(body, str) else json.dumps(body, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _roles_section(cfn: Any) -> dict[str, Any]:
    stack = cfn.describe_stacks(StackName=ROLES_STACK)["Stacks"][0]
    return {"stackName": ROLES_STACK, "status": stack.get("StackStatus"),
            "parameters": stack.get("Parameters") or [], "outputs": stack.get("Outputs") or [],
            "templateSha256": template_hash(cfn)}


def read_environment(configuration: Mapping[str, Any], label: str = "the Lambda") -> dict[str, str]:
    """A Lambda's environment variables, without the restart marker.

    Raises:
        SnapshotError: When Lambda returned an error instead of the variables, or an
            environment with no variables (for example a KMS key that cannot decrypt them):
            what is not read in full is never saved or replaced.
    """
    environment = configuration.get("Environment")
    if environment is None:
        return {}
    if not isinstance(environment, Mapping) or environment.get("Error") \
            or "Variables" not in environment:
        raise SnapshotError(
            f"{label}: the function's configuration cannot read its environment in full (Lambda "
            "returned an error or no variables, for example when a customer-managed KMS key "
            "cannot decrypt them); nothing was saved or changed for it")
    variables = dict(environment["Variables"] or {})
    variables.pop(lambda_release.MARKER, None)
    return variables


def _lambdas_section(clients: Clients, where: Where) -> dict[str, Any]:
    value = clients.ssm.get_parameter(Name=lambda_release.SSM_SECRET_PARAMETER)["Parameter"]
    if not str(value.get("Value", "")).startswith("arn:"):
        raise SnapshotError(f"{lambda_release.SSM_SECRET_PARAMETER} does not hold a secret ARN; "
                            "the snapshot saves only references and will not print the value")
    holds_arn = lambda_release.holds_function_arn(clients.control, where.gateway_id)
    holds = lambda_release.require_holds(clients.lam, holds_arn, where.account, where.region)
    semantic = clients.lam.get_function_configuration(FunctionName=lambda_release.SEMANTIC_FUNCTION)
    return {
        "ssmSecretArn": {"name": lambda_release.SSM_SECRET_PARAMETER, "type": value["Type"],
                         "value": value["Value"]},
        "holds": {"arn": holds_arn, "environment": read_environment(holds, "Lambda holds")},
        "semantic": {"name": lambda_release.SEMANTIC_FUNCTION,
                     "environment": read_environment(semantic, "Lambda semantic")},
    }


def detected_mode(gateway: Mapping[str, Any]) -> str:
    """``jwt`` when the Gateway has the Cognito authorizer, else ``iam``."""
    return JWT if gateway.get("authorizerType") == "CUSTOM_JWT" else IAM


def take(clients: Clients, where: Where, *, now: datetime, commit: str,
         hosted_release_path: Path) -> dict[str, Any]:
    """Read everything the release replaces. Only reads; returns a plain, redacted document.

    Raises:
        SnapshotError: When a hop cannot be saved in full or holds a reference it should not.
    """
    state = preflight.read_state(clients.control, where.gateway_id, where.runtime_ids)
    mode = detected_mode(state.gateway if isinstance(state.gateway, Mapping) else {})
    target = preflight.target_for(mode, where.env, where.account, where.region)
    service = _service_section(clients.apprunner, where.service_arn)
    document = _json_safe({
        "schema": SCHEMA, "takenAt": as_utc(now).isoformat(), "commit": commit,
        "account": where.account, "region": where.region, "mode": mode,
        "gateway": _gateway_section(state, where, target),
        "runtimes": {name: _runtime_section(name, described)
                     for name, described in state.runtimes.items()},
        "service": service, "site": _site_section(clients.cloudfront, hosted_release_path),
        "roles": _roles_section(clients.cfn), "lambdas": _lambdas_section(clients, where),
        "policies": state.policies,
    })
    baseline = preflight.hop_findings(state, target)
    variables, secrets = preflight.image_environment(service) or ({}, {})
    baseline += preflight.check_service_environment(variables, secrets, target)
    document, redacted = redact(document)
    document.update({"baselineFindings": baseline, "redacted": sorted(redacted),
                     "complete": True})
    return document


# ------------------------------------------------------- writing, loading, finding


def _digest(document: Mapping[str, Any]) -> str:
    body = {key: value for key, value in document.items() if key != "integrity"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def write(saved: Mapping[str, Any], directory: Path, now: datetime) -> Path:
    """Write the snapshot privately under a name that sorts by time; never overwrite one.

    Raises:
        SnapshotError: When a snapshot with the same name exists.
    """
    private_dir(directory)
    directory.chmod(0o700)
    path = directory / f"snapshot-{utc_stamp(now)}.json"
    if path.exists():
        raise SnapshotError(f"{path.name} already exists; a saved snapshot is never overwritten")
    document = {**saved, "integrity": {"algorithm": "sha256", "digest": _digest(saved)}}
    write_private(path, json.dumps(document, indent=2, sort_keys=True) + "\n")
    return path


def _read_document(path: Path) -> dict[str, Any]:
    try:
        mode = path.stat().st_mode
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SnapshotError(
            f"cannot read the snapshot {path.name}: {type(error).__name__}") from None
    if mode & 0o077:
        raise SnapshotError(f"the snapshot {path.name} can be read by others (mode "
                            f"{mode & 0o777:03o}); it must be mode 0600, run chmod 600 on it")
    if not isinstance(document, dict):
        raise SnapshotError(f"the snapshot {path.name} is not a JSON object")
    if document.get("schema") != SCHEMA:
        raise SnapshotError(f"the snapshot {path.name} has an unknown schema")
    return document


def load(path: Path) -> dict[str, Any]:
    """Read a snapshot and refuse one that is unreadable, open to others, changed, incomplete,
    malformed or foreign.

    Raises:
        SnapshotError: With the file name and the reason.
    """
    document = _read_document(path)
    claimed = document.pop("integrity", None)
    if not isinstance(claimed, dict) or claimed.get("digest") != _digest(document):
        raise SnapshotError(f"the snapshot {path.name} fails its integrity check; it was "
                            "changed or cut short, take a new one")
    if document.get("complete") is not True:
        raise SnapshotError(f"the snapshot {path.name} is not complete")
    missing = [key for key in SECTIONS if key not in document]
    if missing:
        raise SnapshotError(f"the snapshot {path.name} lacks {', '.join(missing)}")
    problems = structure_problems(document)
    if problems:
        raise SnapshotError(f"the snapshot {path.name} is malformed: {'; '.join(problems)}")
    return document


def structure_problems(document: Mapping[str, Any]) -> list[str]:
    """Where the document's top level is not the shape the rollback reads (names, not values)."""
    problems = [f"{key} is not a {kind.__name__}" for key, kind in TOP_LEVEL_TYPES
                if not isinstance(document.get(key), kind)]
    if document.get("mode") not in MODES:
        problems.append("mode is not iam or jwt")
    runtimes = document.get("runtimes")
    if isinstance(runtimes, dict) and not all(isinstance(r, dict) for r in runtimes.values()):
        problems.append("a Runtime is not an object")
    service = document.get("service")
    if isinstance(service, dict) and not isinstance(service.get("ServiceArn"), str):
        problems.append("service has no ServiceArn")
    return problems


def latest_complete(directory: Path) -> tuple[Path | None, list[str]]:
    """The newest snapshot that loads, and the names of newer ones that were skipped."""
    if not directory.is_dir():
        return None, []
    skipped: list[str] = []
    for path in sorted((p for p in directory.iterdir() if NAME.fullmatch(p.name)), reverse=True):
        try:
            load(path)
        except SnapshotError:
            skipped.append(path.name)
            continue
        return path, skipped
    return None, skipped


# -------------------------------------------------------------------- the command


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The ``snapshot`` command's flags."""
    parser.add_argument("--service-arn", required=True, help="the App Runner service to save")
    parser.add_argument("--accept-baseline", action="store_true",
                        help="save the snapshot even though some hops already report findings "
                             "(a release that is under way, or drift): it then holds that state")


def command(args: argparse.Namespace, deps: Any, say: Callable[[str], None]) -> int:
    """Save the replaced configuration to the release folder. Only reads AWS."""
    env = deps.env
    account, region = settings.deployment_target(env)
    if not settings.REGION.fullmatch(region):
        raise settings.ReleaseConfigError(
            "the Region in AURORA_CLUSTER_ARN is not a Region name; check meridian/.env")
    shape = preflight.Target(mode=IAM, design=settings.BOTH, account=account, region=region)
    problems = preflight.check_service_arn(args.service_arn, shape)
    if problems:
        raise SnapshotError(problems[0])
    gateway_id, runtime_ids = preflight.hop_ids(env)
    session = deps.session(region)
    require_account(session.client("sts"), env["AURORA_CLUSTER_ARN"])
    clients = Clients(
        control=session.client("bedrock-agentcore-control"), apprunner=session.client("apprunner"),
        cloudfront=session.client("cloudfront"), cfn=session.client("cloudformation"),
        ssm=session.client("ssm"), lam=session.client("lambda"))
    where = Where(account, region, gateway_id, runtime_ids, args.service_arn, env)
    saved = take(clients, where, now=deps.now(), commit=deps.head_sha(),
                 hosted_release_path=deps.hosted_release_path)
    for line in saved["baselineFindings"]:
        say(f"  BASELINE  {line}")
    if saved["baselineFindings"] and not args.accept_baseline:
        say(f"NOT saved: {len(saved['baselineFindings'])} finding(s) are already present, so the "
            "hops are not in one clean state (a release under way, or drift). Fix them, or save "
            "this state on purpose with --accept-baseline.")
        return 1
    path = write(saved, deps.release_dir, deps.now())
    report(saved, path, say)
    return 0


def report(saved: Mapping[str, Any], path: Path, say: Callable[[str], None]) -> None:
    """Say what was saved and what was redacted."""
    say(f"Saved {path} (mode {saved['mode']}, commit {str(saved['commit'])[:12]}, "
        f"{len(saved['redacted'])} plain value(s) redacted, "
        f"{len(saved['baselineFindings'])} finding(s) already present)")
    for item in saved["redacted"]:
        say(f"  REDACTED  {item}: the rollback cannot restore this hop automatically; move the "
            "value into a secret reference")
