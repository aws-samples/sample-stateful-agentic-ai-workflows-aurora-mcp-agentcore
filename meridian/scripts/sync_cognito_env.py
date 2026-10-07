"""Copy the identity stack's outputs into the files that read them.

After ``cdk deploy`` of MeridianIdentity this writes:
  meridian/.env                         MERIDIAN_COGNITO_REGION, _USER_POOL_ID, _APP_CLIENT_ID
  meridian/frontend/.env.development.local   VITE_COGNITO_DOMAIN, VITE_COGNITO_CLIENT_ID

The frontend file is the development-mode one on purpose: ``vite build`` never reads it, so the
hosted bundle stays on the shared-token path until the cutover release moves these two lines
to .env.production.local. Without --write it prints what it would write.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.provision_service_logins import (  # noqa: E402
    ENV_FILE,
    redact,
    require_account,
    write_env,
)

STACK_NAME = "MeridianIdentity"
FRONTEND_ENV_FILE = Path(__file__).resolve().parents[1] / "frontend" / ".env.development.local"


def stack_outputs(cloudformation, stack_name: str = STACK_NAME) -> Dict[str, str]:
    """The stack's outputs by key."""
    stacks = cloudformation.describe_stacks(StackName=stack_name)["Stacks"]
    return {o["OutputKey"]: o["OutputValue"] for o in stacks[0].get("Outputs", [])}


def settings_from(outputs: Dict[str, str], region: str) -> Dict[Path, Dict[str, str]]:
    """The lines to write into each file."""
    missing = [k for k in ("UserPoolId", "AppClientId", "HostedUiDomain") if k not in outputs]
    if missing:
        raise SystemExit(f"{STACK_NAME} has no output {', '.join(missing)}; is it deployed?")
    return {
        ENV_FILE: {
            "MERIDIAN_COGNITO_REGION": region,
            "MERIDIAN_COGNITO_USER_POOL_ID": outputs["UserPoolId"],
            "MERIDIAN_COGNITO_APP_CLIENT_ID": outputs["AppClientId"],
        },
        FRONTEND_ENV_FILE: {
            "VITE_COGNITO_DOMAIN": outputs["HostedUiDomain"],
            "VITE_COGNITO_CLIENT_ID": outputs["AppClientId"],
        },
    }


def _run(write: bool) -> None:
    load_dotenv(ENV_FILE)
    cluster_arn = os.environ["AURORA_CLUSTER_ARN"]
    region = cluster_arn.split(":")[3]
    require_account(boto3.client("sts", region_name=region), cluster_arn)
    outputs = stack_outputs(boto3.client("cloudformation", region_name=region))
    for path, values in settings_from(outputs, region).items():
        for key, value in values.items():
            if write:
                write_env(path, key, value)
            print(f"{'wrote' if write else 'would write'} {key} in {path.name}")


def main(argv: Optional[list] = None) -> None:
    """Read the outputs and print or write them."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="write the files")
    args = parser.parse_args(argv)
    try:
        _run(args.write)
    except ClientError as err:
        code = err.response.get("Error", {}).get("Code", "unknown")
        raise SystemExit(
            f"{err.operation_name} failed ({code}): {redact(str(err))}; "
            f"check AWS_PROFILE and that {STACK_NAME} is deployed"
        ) from None
    except BotoCoreError as err:
        raise SystemExit(
            f"AWS call failed: {redact(str(err))}; check AWS_PROFILE, the region and the network"
        ) from None
    except KeyError as err:
        raise SystemExit(
            f"setting missing {redact(str(err))}; add it to meridian/.env or export it"
        ) from None


if __name__ == "__main__":
    main()
