#!/usr/bin/env python3
"""Publish the Aurora connection settings the MeridianHolds Lambda reads from SSM.

The CDK-built gateway Lambda has no environment variables of its own, so the
cluster ARN, the secret ARN and the database name live in Parameter Store under
/meridian/aurora/. Values come from meridian/.env, the same file the backend
reads. Nothing secret is published: a secret ARN is a pointer, not a secret.

By default /meridian/aurora/secret_arn names the master login's secret. With
--gateway-login it names the meridian_gateway login's secret instead
(AURORA_GATEWAY_SECRET_ARN), which is what the MeridianHolds Lambda reads at its next cold
start. Run it with that flag only in the release that moves the Lambdas off the master login.

Usage:
    cd meridian
    python scripts/publish_gateway_parameters.py
    python scripts/publish_gateway_parameters.py --gateway-login
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import boto3
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PARAMETERS = {
    "/meridian/aurora/cluster_arn": "AURORA_CLUSTER_ARN",
    "/meridian/aurora/secret_arn": "AURORA_SECRET_ARN",
    "/meridian/aurora/database": "AURORA_DATABASE",
}


GATEWAY_LOGIN_SECRET = {"/meridian/aurora/secret_arn": "AURORA_GATEWAY_SECRET_ARN"}


def parameters_from_env(env: dict, *, gateway_login: bool = False) -> dict[str, str]:
    """Map the backend environment to SSM parameter names, failing on any gap.

    Args:
        env: The merged ``meridian/.env`` and process environment.
        gateway_login: Publish the meridian_gateway login's secret instead of the master's.
    """
    sources = {**PARAMETERS, **(GATEWAY_LOGIN_SECRET if gateway_login else {})}
    missing = [key for key in sources.values() if not (env.get(key) or "").strip()]
    if missing:
        raise SystemExit(f"Missing in meridian/.env: {', '.join(missing)}")
    return {name: env[key].strip() for name, key in sources.items()}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--gateway-login", action="store_true",
        help="publish the meridian_gateway login's secret instead of the master's",
    )
    args = parser.parse_args(argv)
    env = {**dotenv_values(Path(__file__).resolve().parents[1] / ".env"), **os.environ}
    region = env.get("AWS_DEFAULT_REGION", "us-east-1")
    ssm = boto3.client("ssm", region_name=region)
    for name, value in parameters_from_env(env, gateway_login=args.gateway_login).items():
        ssm.put_parameter(
            Name=name,
            Value=value,
            Type="String",
            Overwrite=True,
            Description="Meridian gateway Lambda configuration",
        )
        print(f"put {name}")


if __name__ == "__main__":
    main()
