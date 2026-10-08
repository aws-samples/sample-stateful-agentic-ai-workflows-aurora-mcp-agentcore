"""Settings the release tools share: the mode, the Gateway enforcement design and the pool.

Every value is read from a mapping (``meridian/.env`` merged with the process environment), never
from ``os.environ`` directly, so a tool can be tested and can report what it would do.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from backend.agentcore.auth_mode import AUTH_MODE_ENV, IAM, MODES

ENFORCEMENT_ENV = "MERIDIAN_GATEWAY_ENFORCEMENT"
BOTH, CEDAR, INTERCEPTOR = "both", "cedar", "interceptor"
ENFORCEMENTS = (BOTH, CEDAR, INTERCEPTOR)
COGNITO_KEYS = (
    "MERIDIAN_COGNITO_REGION",
    "MERIDIAN_COGNITO_USER_POOL_ID",
    "MERIDIAN_COGNITO_APP_CLIENT_ID",
)

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
    return CognitoSettings(*(values[key] for key in COGNITO_KEYS))
