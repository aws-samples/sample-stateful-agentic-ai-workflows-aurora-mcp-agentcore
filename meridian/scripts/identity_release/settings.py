"""Settings the release tools share: the mode, the Gateway enforcement design and the pool.

Every value is read from a mapping (``meridian/.env`` merged with the process environment), never
from ``os.environ`` directly, so a tool can be tested and can report what it would do.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from backend.agentcore.auth_mode import AUTH_MODE_ENV, IAM, JWT, MODES

ENFORCEMENT_ENV = "MERIDIAN_GATEWAY_ENFORCEMENT"
BOTH, CEDAR, INTERCEPTOR = "both", "cedar", "interceptor"
ENFORCEMENTS = (BOTH, CEDAR, INTERCEPTOR)
COGNITO_KEYS = (
    "MERIDIAN_COGNITO_REGION",
    "MERIDIAN_COGNITO_USER_POOL_ID",
    "MERIDIAN_COGNITO_APP_CLIENT_ID",
)

PROJECT_NAME = "meridianv2"
GATEWAY_LOGICAL_NAMES = {IAM: "meridian-aurora", JWT: "meridian-aurora-jwt"}
HOLDS_TARGET = "MeridianHolds"

CONFIRM_FLAG = "--i-understand-this-changes-aws"
INTERCEPTOR_FUNCTION = "meridian-gateway-traveler-pin"
MERIDIAN_DIR = Path(__file__).resolve().parents[2]
AGENTCORE_DIR = MERIDIAN_DIR / "meridian_agentcore"
AGENTCORE_BIN = "/opt/homebrew/bin/agentcore"
RELEASE_DIR = MERIDIAN_DIR / ".local" / "release-b2"
PROOF_PATH = RELEASE_DIR / "backend-login-proof.json"
PROOF_FIELDS = {
    "ok": "true (the boolean) only when every check passed",
    "at": "ISO 8601 time of the run with a UTC offset; more than 5 minutes ahead of now, or "
          "older than 7 days, is refused",
    "account": "the 12-digit AWS account the run used; must equal the cluster's account",
    "region": "the AWS Region the run used; must equal the cluster's Region",
    "user_pool_id": "the Cognito user pool the run used; must equal MERIDIAN_COGNITO_USER_POOL_ID",
    "git_sha": "the full commit sha of the repository HEAD when the run started; must equal "
               "the HEAD of the release",
    "login": "the database login the backend ran as; must be meridian_backend",
    "checks": "object of check name to true; non-empty and every value true",
}

REGION = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d+$")
POOL_ID = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d+_[A-Za-z0-9]+$")
CLIENT_ID = re.compile(r"^[A-Za-z0-9]{1,128}$")


class ReleaseConfigError(ValueError):
    """A setting the release depends on is missing or malformed."""


def _setting(env: Mapping[str, str | None], key: str) -> str:
    return (env.get(key) or "").strip()


def release_mode(env: Mapping[str, str | None]) -> str:
    """The identity mode: ``iam`` unless MERIDIAN_AGENTCORE_AUTH says ``jwt``.

    Raises:
        ReleaseConfigError: When the setting is neither ``iam`` nor ``jwt``.
    """
    raw = _setting(env, AUTH_MODE_ENV).lower()
    if not raw:
        return IAM
    if raw not in MODES:
        raise ReleaseConfigError(
            f"{AUTH_MODE_ENV} must be 'iam' or 'jwt', not '{raw}'; unset it to keep today's "
            "IAM configuration"
        )
    return raw


def enforcement(env: Mapping[str, str | None]) -> str:
    """Which Gateway layers pin the traveler in ``jwt`` mode.

    The design is ``both`` (the default), ``cedar`` or ``interceptor``.

    Raises:
        ReleaseConfigError: When the setting is none of the three.
    """
    raw = _setting(env, ENFORCEMENT_ENV).lower()
    if not raw:
        return BOTH
    if raw not in ENFORCEMENTS:
        raise ReleaseConfigError(
            f"{ENFORCEMENT_ENV} must be one of {', '.join(ENFORCEMENTS)}, not '{raw}'; the "
            "throwaway-Gateway harness verdicts decide which"
        )
    return raw


def deployment_target(env: Mapping[str, str | None]) -> tuple[str, str]:
    """The account and Region of the deployment, from the Aurora cluster ARN.

    Raises:
        ReleaseConfigError: When AURORA_CLUSTER_ARN is missing or not a cluster ARN.
    """
    parts = _setting(env, "AURORA_CLUSTER_ARN").split(":")
    if len(parts) < 6 or parts[2] != "rds" or not re.fullmatch(r"\d{12}", parts[4]):
        raise ReleaseConfigError("AURORA_CLUSTER_ARN must be an Aurora cluster ARN; set it in "
                                 "meridian/.env")
    return parts[4], parts[3]


def gateway_logical_name(mode: str) -> str:
    """The Gateway's name in ``agentcore.json``; the CDK builds its logical id from it.

    The ``jwt`` Gateway is a different resource from the ``iam`` one because CloudFormation cannot
    change an existing Gateway's authorizer type: the only way to a token authorizer is a new
    Gateway.

    Raises:
        ReleaseConfigError: When ``mode`` is neither ``iam`` nor ``jwt``.
    """
    try:
        return GATEWAY_LOGICAL_NAMES[mode]
    except KeyError:
        raise ReleaseConfigError(
            f"unknown release mode {mode!r}; expected 'iam' or 'jwt'") from None


def gateway_physical_name(mode: str) -> str:
    """The name ``get_gateway`` reports: the CDK prefixes the project name."""
    return f"{PROJECT_NAME}-{gateway_logical_name(mode)}"


def gateway_url_variable(mode: str) -> str:
    """The Runtime environment variable the CDK sets to the Gateway's URL.

    The CDK names it after the Gateway, so each mode's Gateway has its own variable and a
    Runtime has exactly one of them.
    """
    return f"AGENTCORE_GATEWAY_{gateway_logical_name(mode).upper().replace('-', '_')}_URL"


def interceptor_arn(account: str, region: str) -> str:
    """The ARN of the traveler-pin interceptor function in this deployment."""
    return f"arn:aws:lambda:{region}:{account}:function:{INTERCEPTOR_FUNCTION}"


def uses_interceptor(design: str) -> bool:
    """Whether the Gateway carries the request interceptor under this design."""
    return design in (BOTH, INTERCEPTOR)


def uses_cedar_binding(design: str) -> bool:
    """Whether the Cedar traveler-binding rule is rendered under this design."""
    return design in (BOTH, CEDAR)


@dataclass(frozen=True)
class CognitoSettings:
    """The pool both Runtimes, the Gateway and the backend trust."""

    region: str
    pool_id: str
    client_id: str

    @property
    def issuer(self) -> str:
        """The ``iss`` claim of every token the pool issues."""
        return f"https://cognito-idp.{self.region}.amazonaws.com/{self.pool_id}"

    @property
    def discovery_url(self) -> str:
        """The OpenID configuration URL the AgentCore authorizers read."""
        return f"{self.issuer}/.well-known/openid-configuration"


def cognito_settings(env: Mapping[str, str | None]) -> CognitoSettings:
    """The pool settings from ``MERIDIAN_COGNITO_*``.

    Raises:
        ReleaseConfigError: When any of the three is missing or malformed. The message names the
            keys, never a value.
    """
    values = {key: _setting(env, key) for key in COGNITO_KEYS}
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise ReleaseConfigError(
            f"{', '.join(missing)} not set; run scripts/sync_cognito_env.py --write after the "
            "identity stack is deployed"
        )
    checks = (
        (COGNITO_KEYS[0], REGION),
        (COGNITO_KEYS[1], POOL_ID),
        (COGNITO_KEYS[2], CLIENT_ID),
    )
    for key, pattern in checks:
        if not pattern.fullmatch(values[key]):
            raise ReleaseConfigError(f"{key} is not in the expected format; check meridian/.env")
    if values[COGNITO_KEYS[1]].split("_", 1)[0] != values[COGNITO_KEYS[0]]:
        raise ReleaseConfigError(
            f"{COGNITO_KEYS[1]} is in a different Region than {COGNITO_KEYS[0]}; the pool ID "
            "starts with its Region, so the discovery URL would not resolve"
        )
    return CognitoSettings(*(values[key] for key in COGNITO_KEYS))


SHA = re.compile(r"[0-9a-f]{40}")


def _git(repo: Path, *argv: str, what: str) -> str:
    try:
        done = subprocess.run(
            ["git", "-C", str(repo), *argv],
            capture_output=True, text=True, check=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseConfigError(
            f"cannot read {what} of {repo} ({type(exc).__name__}); the backend login "
            "proof is bound to a commit") from exc
    return done.stdout


def git_head(repo: Path = MERIDIAN_DIR) -> str:
    """The full sha of the repository's HEAD commit, which a proof receipt must name.

    Raises:
        ReleaseConfigError: When ``repo`` is not a git work tree, git is not installed or the
            output is not a 40-character lowercase hexadecimal sha.
    """
    sha = _git(repo, "rev-parse", "HEAD", what="the git HEAD").strip()
    if not SHA.fullmatch(sha):
        raise ReleaseConfigError(
            f"the git HEAD of {repo} is not a full commit sha; the backend login proof is "
            "bound to a commit")
    return sha


def working_tree_changes(repo: Path = MERIDIAN_DIR) -> list[str]:
    """The `git status --porcelain` lines for tracked or untracked changes under ``repo``.

    Ignored files and anything under ``.local/`` are not changes. A receipt names HEAD, so a
    run on a tree that differs from HEAD proves nothing about that commit.

    Raises:
        ReleaseConfigError: When git status cannot be read.
    """
    output = _git(repo, "status", "--porcelain", "--untracked-files=all", "--", ".",
                  ":(exclude).local", what="the git status")
    return [line for line in output.splitlines() if line.strip()]
