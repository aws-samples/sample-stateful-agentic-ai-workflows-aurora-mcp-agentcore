"""Read the signed-in traveler from the access token an AgentCore Runtime forwards.

A Runtime configured with a JWT authorizer has already verified the token's signature, issuer,
app client and expiry before its code runs, and the Gateway verifies it again on every tool call.
This module therefore decodes the payload without checking the signature, as the AgentCore
documentation describes for Runtime code. It still refuses input a verified token cannot be: wrong
shape, wrong token type, no traveler claim, a traveler id outside the allowed pattern, or a token
that is about to expire (so a run never starts on a token the Gateway will refuse).

This module must NOT be used for authentication decisions. It trusts that a JWT authorizer already
proved the token, and it must never run on a token that did not pass one first. The backend, which
is the first verifier, uses ``backend.cognito_auth`` instead.

Stdlib only: the MeridianWorkflow Runtime bundles this module. No message produced here contains a
token.
"""

from __future__ import annotations

import base64
import json
import math
import re
import time
from typing import Callable, Optional

from backend.agentcore.errors import CallerTokenExpired

TRAVELER_CLAIM = "traveler_id"
TRAVELER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,50}$")
EXPIRY_MARGIN_SECONDS = 10
MAX_TOKEN_CHARS = 8192


class CallerClaimsError(ValueError):
    """The forwarded token cannot be what a JWT authorizer passed.

    Attributes:
        reason: A short code that is safe to log: ``malformed``, ``token_use``, ``missing_claim``
            or ``traveler_claim``.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"The forwarded access token was refused: {reason}.")
        self.reason = reason


def decode_claims(token: str) -> dict:
    """The payload of ``token``, with no signature check.

    Raises:
        CallerClaimsError: The token is not three base64url parts whose middle part is a JSON
            object.
    """
    if not isinstance(token, str) or len(token) > MAX_TOKEN_CHARS or not token.isascii():
        raise CallerClaimsError("malformed")
    parts = token.split(".")
    if len(parts) != 3:
        raise CallerClaimsError("malformed")
    try:
        payload = base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4))
        claims = json.loads(payload)
    except (ValueError, RecursionError) as exc:
        raise CallerClaimsError("malformed") from exc
    if not isinstance(claims, dict):
        raise CallerClaimsError("malformed")
    return claims


def _expiry(claims: dict) -> float:
    exp = claims.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        raise CallerClaimsError("missing_claim")
    try:
        expiry = float(exp)
    except OverflowError as exc:
        raise CallerClaimsError("missing_claim") from exc
    if not math.isfinite(expiry):
        raise CallerClaimsError("missing_claim")
    return expiry


def _check_unexpired(claims: dict, now: Optional[Callable[[], float]]) -> None:
    if _expiry(claims) <= (now or time.time)() + EXPIRY_MARGIN_SECONDS:
        raise CallerTokenExpired("The caller's access token has expired. Sign in again.")


def ensure_unexpired(token: str, *, now: Optional[Callable[[], float]] = None) -> None:
    """Raise unless the token stays valid for at least ``EXPIRY_MARGIN_SECONDS`` more seconds.

    Raises:
        CallerClaimsError: The token cannot be decoded or has no ``exp``.
        CallerTokenExpired: The token has expired or is about to.
    """
    _check_unexpired(decode_claims(token), now)


def traveler_from_token(token: str, *, now: Optional[Callable[[], float]] = None) -> str:
    """The traveler a forwarded access token proves.

    Raises:
        CallerClaimsError: The token is malformed, is not an access token, or carries no valid
            ``traveler_id`` claim.
        CallerTokenExpired: The token has expired or is about to.
    """
    claims = decode_claims(token)
    if claims.get("token_use") != "access":
        raise CallerClaimsError("token_use")
    _check_unexpired(claims, now)
    traveler_id = claims.get(TRAVELER_CLAIM)
    if not isinstance(traveler_id, str) or not TRAVELER_ID_PATTERN.fullmatch(traveler_id):
        raise CallerClaimsError("traveler_claim")
    return traveler_id
