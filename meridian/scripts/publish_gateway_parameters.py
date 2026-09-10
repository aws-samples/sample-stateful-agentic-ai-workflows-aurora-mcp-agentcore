#!/usr/bin/env python3
"""Publish the Aurora connection settings the MeridianHolds Lambda reads from SSM.

The CDK-built gateway Lambda has no environment variables of its own, so the
cluster ARN, the secret ARN and the database name live in Parameter Store under
/meridian/aurora/. Values come from meridian/.env, the same file the backend
reads. Nothing secret is published: a secret ARN is a pointer, not a secret.

Usage:
    cd meridian
    python scripts/publish_gateway_parameters.py
"""

from __future__ import annotations

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


def parameters_from_env(env: dict) -> dict[str, str]:
    """Map the backend environment to SSM parameter names, failing on any gap."""
    missing = [key for key in PARAMETERS.values() if not (env.get(key) or "").strip()]
    if missing:
        raise SystemExit(f"Missing in meridian/.env: {', '.join(missing)}")
    return {name: env[key].strip() for name, key in PARAMETERS.items()}


def main() -> None:
    env = {**dotenv_values(Path(__file__).resolve().parents[1] / ".env"), **os.environ}
    region = env.get("AWS_DEFAULT_REGION", "us-east-1")
    ssm = boto3.client("ssm", region_name=region)
    for name, value in parameters_from_env(env).items():
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
