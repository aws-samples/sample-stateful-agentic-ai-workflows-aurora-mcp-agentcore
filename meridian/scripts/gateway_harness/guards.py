"""Refuse to run the harness against anything but a fresh throwaway name in the right account."""

from __future__ import annotations

import re
import secrets
from typing import Callable, Mapping, Optional

REAL_GATEWAY_NAME = "meridian-aurora"
REAL_PROJECT_NAME = "meridianv2"
THROWAWAY_PREFIX = "meridian-throwaway-"
THROWAWAY_NAME = re.compile(r"^meridian-throwaway-[0-9a-f]{8}$")
CLUSTER_ARN = re.compile(
    r"^arn:aws[a-z-]*:rds:(?P<region>[a-z0-9-]+):(?P<account>\d{12}):cluster:.+$"
)


class HarnessRefusal(RuntimeError):
    """The harness will not run, or will not touch the named resource."""


def new_throwaway_name(token_hex: Callable[[int], str] = secrets.token_hex) -> str:
    """A fresh name such as ``meridian-throwaway-1a2b3c4d``."""
    return f"{THROWAWAY_PREFIX}{token_hex(4)}"


def check_name(name: str) -> str:
    """Return ``name`` if it is a throwaway name, else refuse.

    Raises:
        HarnessRefusal: The name is the real Gateway, names the real project, or is not
            ``meridian-throwaway-`` followed by eight hex digits.
    """
    if name == REAL_GATEWAY_NAME or REAL_PROJECT_NAME in name:
        raise HarnessRefusal(
            f"refusing '{name}': it names the real Gateway or project. The harness only creates "
            f"and deletes '{THROWAWAY_PREFIX}<8 hex digits>'."
        )
    if not THROWAWAY_NAME.fullmatch(name):
        raise HarnessRefusal(
            f"refusing '{name}': a throwaway name is '{THROWAWAY_PREFIX}' plus eight hex digits."
        )
    return name


def deployment_target(env: Mapping[str, Optional[str]]) -> tuple[str, str]:
    """The account and region the deployment uses, from ``AURORA_CLUSTER_ARN``.

    Raises:
        HarnessRefusal: The setting is missing or not an Aurora cluster ARN.
    """
    match = CLUSTER_ARN.match((env.get("AURORA_CLUSTER_ARN") or "").strip())
    if not match:
        raise HarnessRefusal(
            "AURORA_CLUSTER_ARN is not set to an Aurora cluster ARN; the harness takes the "
            "account and region it may touch from it (meridian/.env)."
        )
    return match["account"], match["region"]


def check_caller(caller_account: str, caller_region: str, expected: tuple[str, str]) -> None:
    """Refuse unless the AWS credentials and region are the deployment's.

    Raises:
        HarnessRefusal: The caller's account or region differs. The message masks the account.
    """
    account, region = expected
    if caller_account != account:
        raise HarnessRefusal(
            "refusing to run: the AWS credentials belong to a different account than "
            "AURORA_CLUSTER_ARN (<acct> expected). Use the claude-code profile."
        )
    if caller_region != region:
        raise HarnessRefusal(
            f"refusing to run: the client region is {caller_region}, the deployment is {region}."
        )
