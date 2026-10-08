#!/usr/bin/env python3
"""Grant the MeridianWorkflow Runtime's execution role access to a seeded traveler.

The workflow Runtime is a workload like the backend and the holds Lambda: before it
sets a traveler scope it must hold an active row in traveler_identity_bindings. The
subject is the role's stable RoleId, which sts:GetCallerIdentity returns as the first
part of UserId inside the Runtime. The role ARN comes from the AgentCore CLI's
deployed state, so run this after ``agentcore deploy``.

The role is bound to Jordan Morgan (trv_meridian_demo) by default. Run it again with
``--traveler trv_demo_decoy`` so Jordan Lee can use the app with their own data. Without
``--apply`` it prints the plan and writes nothing.

Usage:
    cd meridian
    python scripts/bind_workflow_runtime.py [--traveler ID]
    python scripts/bind_workflow_runtime.py [--traveler ID] --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

import boto3
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.bind_current_identity import add_traveler_arguments, bind  # noqa: E402
from scripts.bind_gateway_workload import role_subject  # noqa: E402

load_dotenv()
RUNTIME_NAME = "MeridianWorkflow"
DEPLOYED_STATE = (
    Path(__file__).resolve().parents[1]
    / "meridian_agentcore" / "agentcore" / ".cli" / "deployed-state.json"
)


def workflow_role_arn(state_path: Path) -> str:
    """The MeridianWorkflow execution role ARN from the CLI's deployed state.

    Args:
        state_path: Path to ``agentcore/.cli/deployed-state.json``.

    Raises:
        SystemExit: The file or the runtime's role is absent, so nothing is deployed.
    """
    missing = f"deploy {RUNTIME_NAME} first: no {RUNTIME_NAME} role in {state_path}"
    if not state_path.is_file():
        raise SystemExit(missing)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    for target in (state.get("targets") or {}).values():
        runtimes = ((target or {}).get("resources") or {}).get("runtimes") or {}
        role_arn = (runtimes.get(RUNTIME_NAME) or {}).get("roleArn")
        if isinstance(role_arn, str) and role_arn:
            return role_arn
    raise SystemExit(missing)


def run(
    state_path: Path, *, traveler_id: str, apply: bool, iam: Any, db: Any, bind: Callable[..., None]
) -> int:
    """Bind the workflow role's RoleId to a seeded traveler and report it, or only report."""
    role_arn = workflow_role_arn(state_path)
    subject_id, principal = role_subject(iam, role_arn)
    role_name = principal.rsplit("/", 1)[-1]
    if not apply:
        print(f"Dry run: would bind the {role_name} role to {traveler_id}. "
              "Re-run with --apply to write it.")
        return 0
    bind(db, traveler_id=traveler_id, provider="aws_iam", subject_id=subject_id,
         principal=principal, allow_decoy=True)
    print(f"Bound the {role_name} role to {traveler_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_traveler_arguments(parser)
    args = parser.parse_args()
    region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    return run(
        DEPLOYED_STATE,
        traveler_id=args.traveler,
        apply=args.apply,
        iam=boto3.client("iam"),
        db=boto3.client("rds-data", region_name=region),
        bind=bind,
    )


if __name__ == "__main__":
    sys.exit(main())
