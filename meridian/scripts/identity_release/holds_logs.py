"""Delete the holds function's retained log group between stage 1 and stage 2 of a replacement.

Stage 1 of the Gateway replacement deletes the holds Lambda, whose physical name is fixed
(``meridianv2-MeridianHolds``). The CDK also created a log group named ``/aws/lambda/`` plus that
name and keeps it when the function goes. Stage 2 creates the function again with a log group of
the same name, and CloudFormation refuses to create a log group that exists. So the group is
deleted in between, without exporting its contents (the owner's decision: they are the old
function's debug logs).

The name is derived from the settings, never searched for by pattern: only a group whose name is
exactly that one is ever listed as the target or deleted, and only once the function is gone.
The same check is the gate of stage 2 (``gate_findings``).
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any

from botocore.exceptions import ClientError

from scripts.identity_release import lambda_release, settings
from scripts.provision_service_logins import require_account

READ_BACK_ATTEMPTS = 3
OK, DRIFT, COULD_NOT_RUN, EXIT_REFUSED = 0, 1, 2, 3
PERMISSIONS = "logs:DescribeLogGroups, logs:DeleteLogGroup and lambda:GetFunctionConfiguration"


class HoldsLogsError(RuntimeError):
    """The log group cannot be deleted now, or is not the one this tool may delete."""


def function_name() -> str:
    """The holds Lambda's fixed physical name: the CDK joins the project and the tool name."""
    return f"{settings.PROJECT_NAME}-{settings.HOLDS_TARGET}"


def log_group_name() -> str:
    """The log group the CDK names after the holds function."""
    return f"/aws/lambda/{function_name()}"


def command() -> str:
    """The command that deletes the group."""
    return (f"python scripts/release_identity.py holds-logs --apply {settings.CONFIRM_FLAG}")


def log_group_exists(logs: Any) -> bool:
    """Whether a log group named exactly ``log_group_name()`` exists.

    The listing is by prefix, so groups that merely start with the name are skipped.
    """
    wanted = log_group_name()
    token = None
    while True:
        extra = {"nextToken": token} if token else {}
        page = logs.describe_log_groups(logGroupNamePrefix=wanted, **extra)
        if any(group.get("logGroupName") == wanted for group in page.get("logGroups") or []):
            return True
        token = page.get("nextToken")
        if not token:
            return False


def function_exists(lam: Any) -> bool:
    """Whether the holds function exists."""
    return lambda_release.function_configuration(lam, function_name()) is not None


def delete_exact(logs: Any, name: str) -> None:
    """Delete the log group ``name`` after checking it is exactly the holds function's.

    Raises:
        HoldsLogsError: When ``name`` is not exactly ``log_group_name()``.
    """
    if name != log_group_name():
        raise HoldsLogsError(
            f"refusing to delete {name}: only the exact name {log_group_name()} may be deleted")
    logs.delete_log_group(logGroupName=name)


def gate_findings(logs: Any, lam: Any) -> list[str]:
    """Why stage 2 must not run yet: the old holds function's log group still exists."""
    if not log_group_exists(logs):
        return []
    if function_exists(lam):
        return [f"the log group {log_group_name()} exists and the holds function "
                f"{function_name()} still exists, which stage 2 would create: the stack is not "
                "where stage 1 leaves it; read the stack status in CloudFormation first"]
    return [f"the log group {log_group_name()} was kept when stage 1 deleted the holds "
            "function, and stage 2 creates a log group of that name, which CloudFormation "
            f"refuses; delete it first (no export is taken): {command()}"]


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """The ``holds-logs`` command takes only the two apply flags, added by the caller."""


def _read_back(logs: Any, sleep: Callable[[float], None]) -> bool:
    """Whether the group is gone, asked a bounded number of times."""
    for attempt in range(READ_BACK_ATTEMPTS):
        if not log_group_exists(logs):
            return True
        if attempt < READ_BACK_ATTEMPTS - 1:
            sleep(2)
    return False


def _delete(logs: Any, say: Callable[[str], None], sleep: Callable[[float], None]) -> int:
    say(f"Deleting the log group {log_group_name()} (its logs are not exported).")
    try:
        delete_exact(logs, log_group_name())
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code")
        raise HoldsLogsError(
            f"AWS refused to delete the log group ({code}); the operator profile needs "
            f"{PERMISSIONS}") from error
    if _read_back(logs, sleep):
        say(f"OK  the log group {log_group_name()} is gone; stage 2 can run")
        return OK
    say(f"DRIFT  the log group {log_group_name()} still exists after the delete; read it in "
        "CloudWatch Logs, then run this command again")
    return DRIFT


def run(args: argparse.Namespace, deps: Any, say: Callable[[str], None]) -> int:
    """Plan, or delete, the holds function's retained log group and read back that it is gone.

    Raises:
        HoldsLogsError: When AWS refuses the delete.
        ReleaseConfigError: When a setting is missing or malformed.
    """
    if args.apply and not args.confirmed:
        say(f"REFUSED: --apply also needs {settings.CONFIRM_FLAG}; it changes AWS.")
        return EXIT_REFUSED
    _, region = settings.deployment_target(deps.env)
    if not settings.REGION.fullmatch(region):
        raise settings.ReleaseConfigError(
            "the Region in AURORA_CLUSTER_ARN is not a Region name; check meridian/.env")
    session = deps.session(region)
    require_account(session.client("sts"), deps.env["AURORA_CLUSTER_ARN"])
    lam, logs = session.client("lambda"), session.client("logs")
    blocked = function_exists(lam)
    present = log_group_exists(logs)
    if not args.apply:
        return _dry_run(blocked, present, say)
    if blocked:
        raise HoldsLogsError(_blocked_text())
    if not present:
        say(f"The log group {log_group_name()} is already absent: nothing to delete.")
        return OK
    return _delete(logs, say, deps.sleep)


def _blocked_text() -> str:
    return (f"the holds function {function_name()} still exists, so its log group is in use: "
            "stage 1 has not deleted it. Deploy stage 1 first, or read the stack status in "
            "CloudFormation")


def _dry_run(blocked: bool, present: bool, say: Callable[[str], None]) -> int:
    say("DRY RUN. Nothing is deleted.")
    say(f"  log group (exact name): {log_group_name()}: {'exists' if present else 'absent'}")
    say(f"  holds function {function_name()}: {'still exists' if blocked else 'gone'}")
    say("  steps: refuse unless the holds function is gone; delete that one log group "
        "(its logs are not exported); read back that it is gone")
    if blocked:
        say(f"BLOCKED  {_blocked_text()}")
    say(f"Delete (ASK FIRST): {command()}")
    return COULD_NOT_RUN if blocked else OK
