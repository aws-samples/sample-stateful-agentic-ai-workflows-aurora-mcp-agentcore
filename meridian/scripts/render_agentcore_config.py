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
    {{GATEWAY_ID}}          --gateway-id, else agentcore/.cli/deployed-state.json
    {{POLICY_ENGINE_ID}}    --policy-engine-id, else the same deployed state

Environment variables take precedence over ``meridian/.env``.

The Cedar policies name the deployed gateway, so a first deployment cannot
include them. Without a gateway ID the script renders that first pass: no
policy engine and no gateway ID variable. Without a policy engine ID it leaves
out only the runtime's policy engine variable. Run it again after each
``agentcore deploy`` until it reports a complete configuration.

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
CONFIG_DIR = MERIDIAN_DIR / "meridian_agentcore" / "agentcore"
SPEC_TEMPLATE = "agentcore.template.json"
TARGETS_TEMPLATE = "aws-targets.template.json"
SPEC_OUTPUT = "agentcore.json"
TARGETS_OUTPUT = "aws-targets.json"
DEPLOYED_STATE = Path(".cli") / "deployed-state.json"

GATEWAY_NAME = "meridian-aurora"
POLICY_ENGINE_NAME = "MeridianGovernance"

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


def account_values(env: dict[str, str | None]) -> dict[str, str]:
    """Resolve the account, region and Aurora ARNs the templates need.

    Args:
        env: Merged ``meridian/.env`` and process environment.

    Returns:
        Values for AWS_ACCOUNT_ID, AWS_REGION, AURORA_CLUSTER_ARN and AURORA_SECRET_ARN.

    Raises:
        ConfigError: When an ARN is missing or malformed, the two ARNs name
            different accounts, or the region is not a region name.
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
    region = (
        (env.get("AGENTCORE_REGION") or env.get("AWS_DEFAULT_REGION") or "").strip()
        or cluster["region"]
    )
    if not REGION.match(region):
        raise ConfigError(f"'{region}' is not an AWS Region name such as us-east-1")
    return {
        "AWS_ACCOUNT_ID": cluster["account"],
        "AWS_REGION": region,
        "AURORA_CLUSTER_ARN": cluster_arn,
        "AURORA_SECRET_ARN": secret_arn,
    }


def deployed_ids(state_path: Path, target: str) -> dict[str, str]:
    """Read the gateway and policy engine IDs the AgentCore CLI recorded after a deploy.

    Args:
        state_path: Path to ``agentcore/.cli/deployed-state.json``.
        target: Deployment target name from ``aws-targets.json``.

    Returns:
        GATEWAY_ID and POLICY_ENGINE_ID when the state records them; empty before a deploy.
    """
    if not state_path.is_file():
        return {}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"{state_path} is not valid JSON ({exc}); re-run agentcore deploy"
        ) from exc
    resources = state.get("targets", {}).get(target, {}).get("resources", {})
    gateway = resources.get("mcp", {}).get("gateways", {}).get(GATEWAY_NAME, {})
    engine = resources.get("policyEngines", {}).get(POLICY_ENGINE_NAME, {})
    found = {
        "GATEWAY_ID": gateway.get("gatewayId"),
        "POLICY_ENGINE_ID": engine.get("policyEngineId"),
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
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Fill both templates and prune what the current deployment stage cannot supply.

    Raises:
        ConfigError: When a required placeholder has no value.
    """
    spec = substitute(spec_template, values)
    targets = substitute(targets_template, values)
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
    args = parser.parse_args(argv)

    env = {**dotenv_values(MERIDIAN_DIR / ".env"), **os.environ}
    spec_template = json.loads((CONFIG_DIR / SPEC_TEMPLATE).read_text(encoding="utf-8"))
    targets_template = json.loads((CONFIG_DIR / TARGETS_TEMPLATE).read_text(encoding="utf-8"))
    try:
        values = account_values(env)
        values.update(deployed_ids(CONFIG_DIR / DEPLOYED_STATE, targets_template[0]["name"]))
        overrides = {"GATEWAY_ID": args.gateway_id, "POLICY_ENGINE_ID": args.policy_engine_id}
        values.update({key: value for key, value in overrides.items() if value})
        spec, targets, notes = render(spec_template, targets_template, values)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    write_json(CONFIG_DIR / SPEC_OUTPUT, spec)
    write_json(CONFIG_DIR / TARGETS_OUTPUT, targets)
    print(f"Wrote {CONFIG_DIR / SPEC_OUTPUT} and {CONFIG_DIR / TARGETS_OUTPUT}")
    print(f"  account {values['AWS_ACCOUNT_ID']}, region {values['AWS_REGION']}")
    for note in notes:
        print(f"  {note}")
    if notes:
        print("Deploy with `agentcore deploy -y`, then run this script again.")
    else:
        print("Configuration complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
