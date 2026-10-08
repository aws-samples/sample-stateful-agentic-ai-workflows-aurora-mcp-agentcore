"""Publish the Aurora connection settings the MeridianHolds Lambda reads from SSM.

The CDK-built gateway Lambda has no environment variables of its own, so the
cluster ARN, the secret ARN and the database name live in Parameter Store under
/meridian/aurora/. Values come from meridian/.env, the same file the backend
reads. Nothing secret is published: a secret ARN is a pointer, not a secret.

By default /meridian/aurora/secret_arn names the master login's secret. With
--gateway-login it names the meridian_gateway login's secret instead
(AURORA_GATEWAY_SECRET_ARN), which is what the MeridianHolds Lambda reads at its next cold
start. Run it with that flag only in the release that moves the Lambdas off the master login.

This changes AWS, so without --apply and the confirmation flag it only prints the three
parameter names. An apply checks that the credentials belong to the account and Region of
AURORA_CLUSTER_ARN before it makes the SSM client, then reads every parameter back (the secret
parameter with the same check ``release_identity.py lambdas`` makes). The Lambda keeps its
cached value until ``release_identity.py lambdas --restart-holds``.

Usage:
    cd meridian
    python scripts/publish_gateway_parameters.py [--gateway-login]
    python scripts/publish_gateway_parameters.py [--gateway-login] --apply \
        --i-understand-this-changes-aws

Exit codes: 0 ok (or a dry run), 1 a parameter did not read back as written, 2 could not run
(a missing or malformed setting, another account, an AWS error), 3 usage error or an apply
without the confirmation flag.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.identity_release import lambda_release, settings  # noqa: E402
from scripts.identity_release.usage import UsageParser  # noqa: E402
from scripts.provision_service_logins import redact, require_account  # noqa: E402

OK, DRIFT, COULD_NOT_RUN, REFUSED = 0, 1, 2, 3
SECRET_NAME = lambda_release.SSM_SECRET_PARAMETER

PARAMETERS = {
    "/meridian/aurora/cluster_arn": "AURORA_CLUSTER_ARN",
    "/meridian/aurora/secret_arn": "AURORA_SECRET_ARN",
    "/meridian/aurora/database": "AURORA_DATABASE",
}

# Only the secret differs for the gateway login: the cluster and database are shared, so
# this one entry overrides PARAMETERS. The merge order in parameters_from_env matters; the
# override must come last or the master secret wins.
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


def read_back(ssm: Any, parameters: dict[str, str], gateway_login: bool) -> list[str]:
    """One line per parameter that does not read back exactly as written. Only reads."""
    named = "meridian_gateway" if gateway_login else "master"
    found = lambda_release.parameter_findings(ssm, parameters[SECRET_NAME],
                                              named)
    for name, value in parameters.items():
        if name != SECRET_NAME:
            stored = ssm.get_parameter(Name=name)["Parameter"]["Value"]
            if stored != value:
                found.append(f"SSM {name}: does not read back as written")
    return found


def write_parameters(ssm: Any, parameters: dict[str, str]) -> None:
    """Put every parameter, overwriting what is there."""
    for name, value in parameters.items():
        ssm.put_parameter(Name=name, Value=value, Type="String", Overwrite=True,
                          Description="Meridian gateway Lambda configuration")
        print(f"put {name}")


def build_parser() -> argparse.ArgumentParser:
    """The command line."""
    parser = UsageParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--gateway-login", action="store_true",
        help="publish the meridian_gateway login's secret instead of the master's")
    parser.add_argument("--apply", action="store_true", help="write the parameters (live)")
    parser.add_argument(settings.CONFIRM_FLAG, action="store_true", dest="confirmed",
                        help="required with --apply: it changes AWS")
    return parser


def _plan(parameters: dict[str, str], gateway_login: bool) -> int:
    login = "the meridian_gateway login's" if gateway_login else "the master login's"
    print(f"DRY RUN. Would put these SSM parameters ({SECRET_NAME} names {login} secret):")
    for name in parameters:
        print(f"  {name}")
    print("Apply (ASK FIRST): python scripts/publish_gateway_parameters.py"
          + (" --gateway-login" if gateway_login else "")
          + f" --apply {settings.CONFIRM_FLAG}")
    return OK



def _apply(env: Mapping[str, str | None], parameters: dict[str, str], gateway_login: bool,
           session: Callable[[str], Any]) -> int:
    _account, region = settings.deployment_target(env)
    opened = session(region)
    require_account(opened.client("sts"), str(env["AURORA_CLUSTER_ARN"]))
    ssm = opened.client("ssm")
    write_parameters(ssm, parameters)
    findings = read_back(ssm, parameters, gateway_login)
    for line in findings:
        print(f"DRIFT  {redact(line)}")
    if not findings:
        print("OK  the parameters read back as written; restart the holds Lambda with "
              "release_identity.py lambdas --restart-holds")
    return DRIFT if findings else OK


def main(argv: list[str] | None = None, env: Mapping[str, str | None] | None = None,
         session: Callable[[str], Any] | None = None) -> int:
    """Print the plan, or with both flags write the parameters and read them back."""
    args = build_parser().parse_args(argv)
    env = env if env is not None else {
        **dotenv_values(Path(__file__).resolve().parents[1] / ".env"), **os.environ}
    session = session or (lambda region: boto3.Session(region_name=region))
    try:
        settings.deployment_target(env)
        parameters = parameters_from_env(dict(env), gateway_login=args.gateway_login)
        if not args.apply:
            return _plan(parameters, args.gateway_login)
        if not args.confirmed:
            print(f"REFUSED: --apply also needs {settings.CONFIRM_FLAG}; it changes AWS.")
            return REFUSED
        return _apply(env, parameters, args.gateway_login, session)
    except (settings.ReleaseConfigError, SystemExit) as exc:
        print(f"error: {redact(str(exc))}", file=sys.stderr)
        return COULD_NOT_RUN
    except (BotoCoreError, ClientError) as exc:
        print(f"error: AWS call failed ({type(exc).__name__}): {redact(str(exc))}; check "
              "AWS_PROFILE and the Region", file=sys.stderr)
        return COULD_NOT_RUN


if __name__ == "__main__":
    sys.exit(main())
