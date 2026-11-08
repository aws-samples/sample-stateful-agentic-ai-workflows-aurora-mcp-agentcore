#!/usr/bin/env python3
"""Apply Meridian's logo and palette to the existing Cognito classic sign-in page."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.provision_service_logins import require_account  # noqa: E402
from scripts.sync_cognito_env import stack_outputs  # noqa: E402


def publish(session, env, apply):
    """Bind to the existing stack and app client; preserve all authentication settings."""
    config = Config(connect_timeout=5, read_timeout=30,
                    retries={"mode": "standard", "total_max_attempts": 2})
    require_account(session.client("sts", config=config), env["AURORA_CLUSTER_ARN"])
    outputs = stack_outputs(session.client("cloudformation", config=config))
    for output, key in (("UserPoolId", "USER_POOL_ID"), ("AppClientId", "APP_CLIENT_ID")):
        if outputs[output] != env.get(f"MERIDIAN_COGNITO_{key}"):
            raise ValueError("Identity stack and local sign-in configuration differ.")
    client = session.client("cognito-idp", config=config)
    target = {"UserPoolId": outputs["UserPoolId"], "ClientId": outputs["AppClientId"]}
    before = client.get_ui_customization(**target)["UICustomization"]
    css = (ROOT / "infra/branding/cognito.css").read_text()
    logo = (ROOT / "infra/branding/meridian-login.png").read_bytes()
    if len(css.encode()) > 3000 or len(logo) > 100000:
        raise ValueError("Branding exceeds the documented Cognito request size budget.")
    print("Cognito classic branding: Meridian logo, dark canvas, readable fields and blue action.")
    print("Authentication, callbacks, tokens and user accounts are unchanged.")
    if not apply:
        print("DRY RUN. Add --apply --i-understand-this-changes-aws to publish.")
        return
    folder = ROOT / ".local/signin-branding"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = folder / "before.json"
    if not backup.exists():
        backup.write_text(json.dumps(before, default=str, indent=2) + "\n")
        backup.chmod(0o600)
    client.set_ui_customization(**target, CSS=css, ImageFile=logo)
    after = client.get_ui_customization(**target)["UICustomization"]
    if after.get("CSS") != css or not after.get("ImageUrl"):
        raise ValueError("Branding readback did not match the requested CSS and logo.")
    print(f"Published and verified. CSS SHA-256: {hashlib.sha256(css.encode()).hexdigest()}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--i-understand-this-changes-aws", action="store_true", dest="confirmed")
    args = parser.parse_args()
    if args.apply and not args.confirmed:
        print("Apply requires --i-understand-this-changes-aws.", file=sys.stderr)
        return 3
    env = {**dotenv_values(ROOT / ".env"), **os.environ}
    try:
        session = boto3.Session(region_name=env["AURORA_CLUSTER_ARN"].split(":")[3])
        publish(session, env, args.apply)
    except ClientError as error:
        print(f"AWS operation failed ({error.response['Error']['Code']}).", file=sys.stderr)
        return 2
    except (BotoCoreError, ValueError, KeyError, OSError) as error:
        print(f"Branding could not complete ({type(error).__name__}).", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
