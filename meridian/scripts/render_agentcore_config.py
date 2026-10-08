#!/usr/bin/env python3
"""Render the AgentCore CLI project files for your AWS account.

The committed templates in ``meridian_agentcore/agentcore/`` carry placeholders
instead of an account, a region and deployed resource IDs. This script fills
them in and writes the two files the AgentCore CLI and its CDK app read:

    meridian_agentcore/agentcore/agentcore.json
    meridian_agentcore/agentcore/aws-targets.json

Both outputs are gitignored. Change the ``*.template.json`` files, then render.

Placeholder sources:

    {{AWS_ACCOUNT_ID}}      the account in AURORA_CLUSTER_ARN
    {{AWS_REGION}}          AGENTCORE_REGION, else AWS_DEFAULT_REGION, else the
                            region in AURORA_CLUSTER_ARN
    {{AURORA_CLUSTER_ARN}}  AURORA_CLUSTER_ARN
    {{AURORA_SECRET_ARN}}   AURORA_SECRET_ARN (the full ARN, with its suffix)
    {{AURORA_WORKFLOW_SECRET_ARN}}
                            AURORA_WORKFLOW_SECRET_ARN, the meridian_workflow
                            login's secret (scripts/provision_workflow_login.py)
    {{AURORA_GATEWAY_SECRET_ARN}}
                            AURORA_GATEWAY_SECRET_ARN, the meridian_gateway
                            login's secret (scripts/provision_service_logins.py);
                            the MeridianHolds policy may read it from the release
                            that moves the gateway Lambdas to that login
    {{MERIDIAN_AGENTCORE_AUTH}}
                            MERIDIAN_AGENTCORE_AUTH: ``iam`` (default) or ``jwt``. In ``jwt`` the
                            Gateway and both Runtimes get a Cognito JWT authorizer (from the
                            MERIDIAN_COGNITO_* settings), both Runtimes allowlist the
                            ``Authorization`` header, and the Cedar traveler-binding policy is
                            included unless MERIDIAN_GATEWAY_ENFORCEMENT=interceptor; in ``iam``
                            none of that is rendered, so the config is the one deployed today
    {{MERIDIAN_GATEWAY_ENFORCEMENT}}
                            ``jwt`` only: ``both`` (default, the interceptor plus the Cedar
                            rule), ``cedar`` (the Cedar rule alone) or ``interceptor`` (no Cedar
                            rule). The interceptor itself is attached out of band, not rendered
    {{GATEWAY_ID}}          --gateway-id, else agentcore/.cli/deployed-state.json
    {{POLICY_ENGINE_ID}}    --policy-engine-id, else the same deployed state

Environment variables take precedence over ``meridian/.env``.

The Cedar policies name the deployed gateway, so a first deployment cannot
include them. Without a gateway ID the script renders that first pass: no
policy engine and no gateway ID variable. Without a policy engine ID it leaves
out only the runtime's policy engine variable. A policy engine ID without a
gateway ID is refused, because the engine it names could not be rendered. Run
it again after each ``agentcore deploy`` until it reports a complete
configuration.

``--tighten`` renders the MeridianHolds Lambda's policy with only the meridian_gateway login's
secret. Use it after the Lambda reads that secret (see scripts/release_identity.py lambdas), or it
stops being able to read the master one while it still uses it.

After it writes both files it stages the MeridianWorkflow Runtime's backend
bundle (scripts/stage_workflow_runtime.py), so a deploy never ships a stale copy.

Usage:
    cd meridian
    python scripts/render_agentcore_config.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

MERIDIAN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MERIDIAN_DIR))

from backend.agentcore.auth_mode import AUTH_MODE_ENV, IAM, JWT, MODES  # noqa: E402
from scripts import stage_workflow_runtime  # noqa: E402
from scripts.identity_release import settings  # noqa: E402

CONFIG_DIR = MERIDIAN_DIR / "meridian_agentcore" / "agentcore"
SPEC_TEMPLATE = "agentcore.template.json"
TARGETS_TEMPLATE = "aws-targets.template.json"
SPEC_OUTPUT = "agentcore.json"
TARGETS_OUTPUT = "aws-targets.json"
DEPLOYED_STATE = Path(".cli") / "deployed-state.json"

GATEWAY_NAME = "meridian-aurora"
POLICY_ENGINE_NAME = "MeridianGovernance"
GATEWAY_ID_KEYS = ("mcp", "gateways", GATEWAY_NAME, "gatewayId")
POLICY_ENGINE_ID_KEYS = ("policyEngines", POLICY_ENGINE_NAME, "policyEngineId")
STATE_REMEDY = "re-run agentcore deploy, or pass --gateway-id and --policy-engine-id"
JSON_TYPES = {
    dict: "an object",
    list: "an array",
    str: "a string",
    bool: "a boolean",
    int: "a number",
    float: "a number",
}

JWT_ONLY_POLICIES = frozenset({"meridian_traveler_binding"})
AUTHORIZATION_HEADER = "Authorization"

PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")
CLUSTER_ARN = re.compile(
    r"^arn:aws[a-z-]*:rds:(?P<region>[a-z0-9-]+):(?P<account>\d{12}):cluster:.+$"
)
SECRET_ARN = re.compile(
    r"^arn:aws[a-z-]*:secretsmanager:(?P<region>[a-z0-9-]+):(?P<account>\d{12})"
    r":secret:.+-[A-Za-z0-9]{6}$"
)
REGION = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d+$")


class ConfigError(ValueError):
    """A value needed to render the AgentCore configuration is missing or malformed."""


def _workflow_secret_arn(env: dict[str, str | None], master_arn: str, account: str) -> str:
    """Validate the meridian_workflow login's secret ARN like the master secret's."""
    arn = (env.get("AURORA_WORKFLOW_SECRET_ARN") or "").strip()
    if not arn:
        raise ConfigError(
            "AURORA_WORKFLOW_SECRET_ARN is not set; run scripts/provision_workflow_login.py "
            "and keep the secret ARN it prints in meridian/.env"
        )
    match = SECRET_ARN.match(arn)
    if not match:
        raise ConfigError(
            "AURORA_WORKFLOW_SECRET_ARN must be the full Secrets Manager ARN, including the "
            "six-character suffix; scripts/provision_workflow_login.py prints it"
        )
    if match["account"] != account:
        raise ConfigError(
            f"AURORA_WORKFLOW_SECRET_ARN is in account {match['account']} but "
            f"AURORA_CLUSTER_ARN is in account {account}; both must belong to the "
            "deployment account"
        )
    if arn == master_arn:
        raise ConfigError(
            "AURORA_WORKFLOW_SECRET_ARN must differ from AURORA_SECRET_ARN: the workflow "
            "Runtime runs as the least-privilege meridian_workflow login, not the master"
        )
    return arn


def _gateway_secret_arn(
    env: dict[str, str | None], master_arn: str, workflow_arn: str, account: str
) -> str:
    """Validate the meridian_gateway login's secret ARN like the other logins' secrets."""
    arn = (env.get("AURORA_GATEWAY_SECRET_ARN") or "").strip()
    if not arn:
        raise ConfigError(
            "AURORA_GATEWAY_SECRET_ARN is not set; run "
            "scripts/provision_service_logins.py --login gateway --apply --write-env "
            "and keep the secret ARN it prints in meridian/.env"
        )
    match = SECRET_ARN.match(arn)
    if not match:
        raise ConfigError(
            "AURORA_GATEWAY_SECRET_ARN must be the full Secrets Manager ARN, including the "
            "six-character suffix; scripts/provision_service_logins.py prints it"
        )
    if match["account"] != account:
        raise ConfigError(
            f"AURORA_GATEWAY_SECRET_ARN is in account {match['account']} but "
            f"AURORA_CLUSTER_ARN is in account {account}; both must belong to the "
            "deployment account"
        )
    if arn in (master_arn, workflow_arn):
        raise ConfigError(
            "AURORA_GATEWAY_SECRET_ARN must differ from AURORA_SECRET_ARN and "
            "AURORA_WORKFLOW_SECRET_ARN: the gateway Lambdas run as the least-privilege "
            "meridian_gateway login"
        )
    return arn


def identity_mode(env: dict[str, str | None]) -> str:
    """The AgentCore identity mode: ``iam`` unless MERIDIAN_AGENTCORE_AUTH says ``jwt``.

    Raises:
        ConfigError: When the setting is neither ``iam`` nor ``jwt``.
    """
    try:
        return settings.release_mode(env)
    except settings.ReleaseConfigError as exc:
        raise ConfigError(str(exc)) from exc


def jwt_values(env: dict[str, str | None]) -> dict[str, str]:
    """The enforcement design and the pool's discovery URL and client, for ``jwt`` mode."""
    try:
        pool = settings.cognito_settings(env)
        design = settings.enforcement(env)
    except settings.ReleaseConfigError as exc:
        raise ConfigError(str(exc)) from exc
    return {
        settings.ENFORCEMENT_ENV: design,
        "COGNITO_DISCOVERY_URL": pool.discovery_url,
        "COGNITO_APP_CLIENT_ID": pool.client_id,
    }


def account_values(env: dict[str, str | None]) -> dict[str, str]:
    """Resolve the account, region and Aurora ARNs the templates need.

    Args:
        env: Merged ``meridian/.env`` and process environment.

    Returns:
        Values for AWS_ACCOUNT_ID, AWS_REGION, AURORA_CLUSTER_ARN, AURORA_SECRET_ARN,
        AURORA_WORKFLOW_SECRET_ARN, AURORA_GATEWAY_SECRET_ARN and MERIDIAN_AGENTCORE_AUTH. In
        ``jwt`` mode also MERIDIAN_GATEWAY_ENFORCEMENT, COGNITO_DISCOVERY_URL and
        COGNITO_APP_CLIENT_ID.

    Raises:
        ConfigError: When an ARN is missing or malformed, an ARN names a different
            account than the cluster, the two secrets are the same, or the region
            is not a region name.
    """
    cluster_arn = (env.get("AURORA_CLUSTER_ARN") or "").strip()
    secret_arn = (env.get("AURORA_SECRET_ARN") or "").strip()
    cluster = CLUSTER_ARN.match(cluster_arn)
    if not cluster:
        raise ConfigError(
            "AURORA_CLUSTER_ARN must be an Aurora cluster ARN such as "
            "arn:aws:rds:us-east-1:123456789012:cluster:meridian; set it in meridian/.env"
        )
    secret = SECRET_ARN.match(secret_arn)
    if not secret:
        raise ConfigError(
            "AURORA_SECRET_ARN must be the full Secrets Manager ARN, including the "
            "six-character suffix, such as "
            "arn:aws:secretsmanager:us-east-1:123456789012:secret:meridian-AbC123"
        )
    if secret["account"] != cluster["account"]:
        raise ConfigError(
            f"AURORA_SECRET_ARN is in account {secret['account']} but AURORA_CLUSTER_ARN "
            f"is in account {cluster['account']}; both must belong to the deployment account"
        )
    workflow_secret_arn = _workflow_secret_arn(env, secret_arn, cluster["account"])
    gateway_secret_arn = _gateway_secret_arn(
        env, secret_arn, workflow_secret_arn, cluster["account"]
    )
    region = (
        (env.get("AGENTCORE_REGION") or env.get("AWS_DEFAULT_REGION") or "").strip()
        or cluster["region"]
    )
    if not REGION.match(region):
        raise ConfigError(f"'{region}' is not an AWS Region name such as us-east-1")
    values = {
        "AWS_ACCOUNT_ID": cluster["account"],
        "AWS_REGION": region,
        "AURORA_CLUSTER_ARN": cluster_arn,
        "AURORA_SECRET_ARN": secret_arn,
        "AURORA_WORKFLOW_SECRET_ARN": workflow_secret_arn,
        "AURORA_GATEWAY_SECRET_ARN": gateway_secret_arn,
        AUTH_MODE_ENV: identity_mode(env),
    }
    if values[AUTH_MODE_ENV] == JWT:
        values.update(jwt_values(env))
    return values


def state_value(state: Any, keys: tuple[str, ...], state_path: Path) -> str | None:
    """Follow ``keys`` through the deployed state to a string.

    Args:
        state: The parsed deployed-state document.
        keys: Object keys from the top level down to the value.
        state_path: The file the state came from, named in errors.

    Returns:
        The string at the end of ``keys``, or None when a key along the way is absent.

    Raises:
        ConfigError: When a level is not a JSON object or the value is not a string.
    """
    node = state
    for depth, key in enumerate(keys):
        if not isinstance(node, dict):
            where = ".".join(keys[:depth]) or "the top level"
            raise ConfigError(
                f"{state_path}: expected an object at {where}, found "
                f"{JSON_TYPES.get(type(node), type(node).__name__)}; {STATE_REMEDY}"
            )
        node = node.get(key)
        if node is None:
            return None
    if not isinstance(node, str):
        raise ConfigError(
            f"{state_path}: {'.'.join(keys)} must be a string, found "
            f"{JSON_TYPES.get(type(node), type(node).__name__)}; {STATE_REMEDY}"
        )
    return node


def deployed_ids(state_path: Path, target: str) -> dict[str, str]:
    """Read the gateway and policy engine IDs the AgentCore CLI recorded after a deploy.

    Args:
        state_path: Path to ``agentcore/.cli/deployed-state.json``.
        target: Deployment target name from ``aws-targets.json``.

    Returns:
        GATEWAY_ID and POLICY_ENGINE_ID when the state records them; empty before a deploy.

    Raises:
        ConfigError: When the file is not JSON or does not have the shape the CLI writes.
    """
    if not state_path.is_file():
        return {}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{state_path} is not valid JSON ({exc}); re-run agentcore deploy"
        ) from exc
    resources = ("targets", target, "resources")
    found = {
        "GATEWAY_ID": state_value(state, (*resources, *GATEWAY_ID_KEYS), state_path),
        "POLICY_ENGINE_ID": state_value(state, (*resources, *POLICY_ENGINE_ID_KEYS), state_path),
    }
    return {key: value for key, value in found.items() if value}


def substitute(node: Any, values: dict[str, str]) -> Any:
    """Replace every known ``{{NAME}}`` placeholder in the strings of a JSON value."""
    if isinstance(node, dict):
        return {key: substitute(value, values) for key, value in node.items()}
    if isinstance(node, list):
        return [substitute(item, values) for item in node]
    if isinstance(node, str):
        return PLACEHOLDER.sub(lambda match: values.get(match[1], match[0]), node)
    return node


def unresolved(node: Any) -> set[str]:
    """Return the placeholder names still present anywhere in a JSON value."""
    if isinstance(node, dict):
        return set().union(*(unresolved(value) for value in node.values()))
    if isinstance(node, list):
        return set().union(*(unresolved(item) for item in node))
    if isinstance(node, str):
        return set(PLACEHOLDER.findall(node))
    return set()


def apply_identity_mode(
    spec: dict[str, Any], mode: str, design: str = settings.BOTH
) -> None:
    """Drop the policies the chosen mode and Gateway design do not use.

    Raises:
        ConfigError: When ``mode`` is not ``iam`` or ``jwt``, or ``design`` is not a known design.

    ``iam`` removes every policy that only makes sense for Cognito callers. ``jwt`` removes the
    traveler-binding rule only under the ``interceptor`` design, where the interceptor pins the
    traveler and Cedar adds nothing.
    """
    if mode not in MODES:
        raise ConfigError(f"{AUTH_MODE_ENV} must be 'iam' or 'jwt', not '{mode}'")
    if design not in settings.ENFORCEMENTS:
        raise ConfigError(
            f"{settings.ENFORCEMENT_ENV} must be one of {', '.join(settings.ENFORCEMENTS)}, "
            f"not '{design}'"
        )
    if mode == JWT and settings.uses_cedar_binding(design):
        return
    for engine in spec.get("policyEngines", []):
        engine["policies"] = [
            policy for policy in engine["policies"] if policy["name"] not in JWT_ONLY_POLICIES
        ]


def jwt_authorizer(discovery_url: str, client_id: str) -> dict[str, Any]:
    """A Cognito access-token authorizer: the ``client_id`` claim, never an audience."""
    return {"customJwtAuthorizer": {"discoveryUrl": discovery_url, "allowedClients": [client_id]}}


def apply_jwt_authorizers(spec: dict[str, Any], values: dict[str, str]) -> None:
    """Move the Gateway and both Runtimes to the Cognito authorizer.

    A Runtime accepts IAM or JWT callers, never both, and the Gateway has one authorizer type, so
    this changes every hop in one render. The Runtimes also allowlist ``Authorization`` so their
    code can read the caller's token and forward it to the Gateway.
    """
    url, client = values["COGNITO_DISCOVERY_URL"], values["COGNITO_APP_CLIENT_ID"]
    for runtime in spec.get("runtimes", []):
        runtime["authorizerType"] = "CUSTOM_JWT"
        runtime["authorizerConfiguration"] = jwt_authorizer(url, client)
        runtime["requestHeaderAllowlist"] = [AUTHORIZATION_HEADER]
    for gateway in spec.get("agentCoreGateways", []):
        gateway["authorizerType"] = "CUSTOM_JWT"
        gateway["authorizerConfiguration"] = jwt_authorizer(url, client)


def tighten_holds_policy(spec: dict[str, Any], values: dict[str, str]) -> None:
    """Leave the MeridianHolds Lambda able to read only the meridian_gateway login's secret."""
    for gateway in spec.get("agentCoreGateways", []):
        for target in gateway.get("targets", []):
            policy = (target.get("compute") or {}).get("iamPolicy")
            if target.get("name") != "MeridianHolds" or not policy:
                continue
            for statement in policy["Statement"]:
                if statement.get("Action") == ["secretsmanager:GetSecretValue"]:
                    statement["Resource"] = [values["AURORA_GATEWAY_SECRET_ARN"]]


def drop_pending_deployment_values(spec: dict[str, Any]) -> list[str]:
    """Remove the parts of the spec that name resources a deploy has not created yet.

    Returns:
        One note per removal, for the operator.
    """
    notes: list[str] = []
    for runtime in spec.get("runtimes", []):
        for env_var in [var for var in runtime.get("envVars", []) if unresolved(var)]:
            runtime["envVars"].remove(env_var)
            notes.append(
                f"runtime {runtime['name']}: left out {env_var['name']} (not deployed yet)"
            )
    if unresolved(spec.get("policyEngines", [])):
        spec["policyEngines"] = []
        for gateway in spec.get("agentCoreGateways", []):
            gateway.pop("policyEngineConfiguration", None)
        notes.append(
            "left out the Cedar policy engine: its policies name the gateway, "
            "which is not deployed yet"
        )
    return notes


def render(
    spec_template: dict[str, Any],
    targets_template: list[dict[str, Any]],
    values: dict[str, str],
    *,
    tighten: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Fill both templates and prune what the current deployment pass cannot supply.

    ``tighten`` leaves the holds Lambda only the meridian_gateway login's secret.

    Raises:
        ConfigError: When a required placeholder has no value, or a policy engine ID
            comes without the gateway ID its policies name, or the identity mode or
            enforcement design in ``values`` is not one of the known choices.
    """
    if values.get("POLICY_ENGINE_ID") and not values.get("GATEWAY_ID"):
        raise ConfigError(
            "a policy engine ID needs the gateway ID too, because the engine's Cedar "
            "policies name the gateway; pass --gateway-id with --policy-engine-id"
        )
    spec = substitute(spec_template, values)
    targets = substitute(targets_template, values)
    mode = values.get(AUTH_MODE_ENV, IAM)
    apply_identity_mode(spec, mode, values.get(settings.ENFORCEMENT_ENV, settings.BOTH))
    if mode == JWT:
        apply_jwt_authorizers(spec, values)
    if tighten:
        tighten_holds_policy(spec, values)
    notes = drop_pending_deployment_values(spec)
    missing = unresolved(spec) | unresolved(targets)
    if missing:
        raise ConfigError(f"no value for placeholder(s): {', '.join(sorted(missing))}")
    return spec, targets, notes


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render the AgentCore CLI config for your account."
    )
    parser.add_argument(
        "--gateway-id", help="deployed gateway ID (default: the CLI deployment state)"
    )
    parser.add_argument(
        "--policy-engine-id",
        help="deployed policy engine ID (default: the CLI deployment state)",
    )
    parser.add_argument(
        "--tighten", action="store_true",
        help="leave the MeridianHolds Lambda only the meridian_gateway login's secret",
    )
    args = parser.parse_args(argv)

    env = {**dotenv_values(MERIDIAN_DIR / ".env"), **os.environ}
    spec_template = json.loads((CONFIG_DIR / SPEC_TEMPLATE).read_text(encoding="utf-8"))
    targets_template = json.loads((CONFIG_DIR / TARGETS_TEMPLATE).read_text(encoding="utf-8"))
    try:
        values = account_values(env)
        values.update(deployed_ids(CONFIG_DIR / DEPLOYED_STATE, targets_template[0]["name"]))
        overrides = {"GATEWAY_ID": args.gateway_id, "POLICY_ENGINE_ID": args.policy_engine_id}
        values.update({key: value for key, value in overrides.items() if value})
        spec, targets, notes = render(
            spec_template, targets_template, values, tighten=args.tighten)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    write_json(CONFIG_DIR / SPEC_OUTPUT, spec)
    write_json(CONFIG_DIR / TARGETS_OUTPUT, targets)
    staged = json.loads(stage_workflow_runtime.stage().read_text(encoding="utf-8"))
    print(f"Wrote {CONFIG_DIR / SPEC_OUTPUT} and {CONFIG_DIR / TARGETS_OUTPUT}")
    print(f"  account {values['AWS_ACCOUNT_ID']}, region {values['AWS_REGION']}")
    print(f"  AgentCore identity mode: {values[AUTH_MODE_ENV]}")
    if values[AUTH_MODE_ENV] == JWT:
        print(f"  Gateway enforcement: {values[settings.ENFORCEMENT_ENV]}")
    if args.tighten:
        print("  Tightened: MeridianHolds may read only the meridian_gateway secret")
    print(f"  staged {len(staged)} workflow modules into the MeridianWorkflow bundle")
    for note in notes:
        print(f"  {note}")
    if notes:
        print("Deploy with `agentcore deploy -y`, then run this script again.")
    else:
        print("Configuration complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
