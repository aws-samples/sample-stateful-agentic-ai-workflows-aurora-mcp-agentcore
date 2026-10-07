"""The one switch between today's IAM calls and the Cognito bearer-token path.

``MERIDIAN_AGENTCORE_AUTH`` is read by the backend, by both AgentCore Runtimes and by the config
renderer. ``iam`` (the default) keeps every hop signed with AWS credentials. ``jwt`` makes every hop
carry the signed-in person's Cognito access token. Any other value is refused, so a typo can never
silently select the weaker path.

This module reads the environment and imports nothing from the rest of the backend, because the
MeridianWorkflow Runtime bundles it.
"""

from __future__ import annotations

import os

AUTH_MODE_ENV = "MERIDIAN_AGENTCORE_AUTH"
IAM = "iam"
JWT = "jwt"
MODES = (IAM, JWT)


class AuthModeError(RuntimeError):
    """``MERIDIAN_AGENTCORE_AUTH`` holds a value other than ``iam`` or ``jwt``."""


def agentcore_auth_mode() -> str:
    """The configured mode: ``iam`` when the variable is unset or blank, else ``iam`` or ``jwt``.

    Raises:
        AuthModeError: The variable is set to anything else.
    """
    raw = os.getenv(AUTH_MODE_ENV)
    if raw is None or not raw.strip():
        return IAM
    mode = raw.strip().lower()
    if mode not in MODES:
        raise AuthModeError(
            f"{AUTH_MODE_ENV} holds an unrecognised mode; set it to 'iam' (today's path) or 'jwt' "
            "(Cognito bearer tokens), or unset it."
        )
    return mode


def jwt_mode() -> bool:
    """True when every AgentCore hop must carry the caller's Cognito access token."""
    return agentcore_auth_mode() == JWT
