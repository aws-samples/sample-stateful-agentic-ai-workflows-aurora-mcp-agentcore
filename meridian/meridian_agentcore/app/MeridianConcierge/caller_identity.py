"""Who is calling this Runtime, when its JWT authorizer forwards the caller's access token.

``MERIDIAN_AGENTCORE_AUTH`` chooses the path: ``iam`` (default) trusts the traveler in the payload,
as before; ``jwt`` takes the traveler from the ``traveler_id`` claim of the access token in the
``Authorization`` header. The Runtime's authorizer has already verified the token, so it is decoded
here without a signature check, and everything a verified token could not be is still refused.

This is the Concierge's copy of ``backend/agentcore/caller_claims.py`` (the Concierge ships without
the backend package); ``tests/test_concierge_identity.py`` runs both against one table of tokens.
Nothing here logs or returns a token.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import time
from typing import Callable, Mapping, Optional

AUTH_MODE_ENV = "MERIDIAN_AGENTCORE_AUTH"
MODES = ("iam", "jwt")
TRAVELER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,50}$")
EXPIRY_MARGIN_SECONDS = 10
MAX_TOKEN_CHARS = 8192
TOKEN_SHAPE = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")


class CallerRefused(Exception):
    """The caller cannot be served.

    Attributes:
        code: ``authorization`` (no usable identity) or ``token_expired``.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def auth_mode() -> str:
    """``iam`` unless the environment says ``jwt``; any other value is refused."""
    raw = (os.getenv(AUTH_MODE_ENV) or "").strip().lower()
    if not raw:
        return "iam"
    if raw not in MODES:
        raise ValueError(f"{AUTH_MODE_ENV} must be 'iam' or 'jwt', not {raw!r}.")
    return raw


def bearer_token(headers: Optional[Mapping[str, str]]) -> Optional[str]:
    """The token of an ``Authorization: Bearer`` header (name matched case-insensitively)."""
    for name, value in (headers or {}).items():
        if isinstance(name, str) and name.lower() == "authorization" and isinstance(value, str):
            scheme, _, token = value.strip().partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
    return None


def _claims(token: str) -> dict:
    refusal = CallerRefused("authorization", "The forwarded access token was refused: malformed.")
    if (
        not isinstance(token, str)
        or len(token) > MAX_TOKEN_CHARS
        or not TOKEN_SHAPE.fullmatch(token)
    ):
        raise refusal
    segment = token.split(".")[1]
    try:
        padded = segment.replace("-", "+").replace("_", "/") + "=" * (-len(segment) % 4)
        claims = json.loads(base64.b64decode(padded, validate=True).decode("utf-8"))
    except (ValueError, RecursionError) as exc:
        raise refusal from exc
    if not isinstance(claims, dict):
        raise refusal
    return claims


def _expiry(claims: dict) -> float:
    refusal = CallerRefused("authorization", "The forwarded access token was refused: exp.")
    exp = claims.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        raise refusal
    try:
        expiry = float(exp)
    except OverflowError as exc:
        raise refusal from exc
    if not math.isfinite(expiry):
        raise refusal
    return expiry


def traveler_from_token(token: str, now: Optional[Callable[[], float]] = None) -> str:
    """The traveler a forwarded access token proves.

    Raises:
        CallerRefused: ``authorization`` for a malformed token, a token that is not an access
            token, or a missing or invalid traveler claim; ``token_expired`` for an expired one.
    """
    claims = _claims(token)
    if claims.get("token_use") != "access":
        raise CallerRefused("authorization", "The forwarded access token was refused: token_use.")
    if _expiry(claims) <= (now or time.time)() + EXPIRY_MARGIN_SECONDS:
        raise CallerRefused(
            "token_expired", "The caller's access token has expired. Sign in again."
        )
    traveler_id = claims.get("traveler_id")
    if not isinstance(traveler_id, str) or not TRAVELER_ID_PATTERN.fullmatch(traveler_id):
        raise CallerRefused("authorization", "The forwarded access token was refused: traveler.")
    return traveler_id


def caller_from_request(
    payload: dict, headers: Optional[Mapping[str, str]], mode: str
) -> tuple[Optional[str], Optional[str]]:
    """The traveler the claim proves and the token to forward, or ``(None, None)`` in ``iam`` mode.

    Raises:
        CallerRefused: In ``jwt`` mode, there is no usable token, it has expired, or the payload
            names a different traveler than the token.
    """
    if mode != "jwt":
        return None, None
    token = bearer_token(headers)
    if token is None:
        raise CallerRefused("authorization", "No signed-in caller: no bearer token was forwarded.")
    traveler_id = traveler_from_token(token)
    named = payload.get("traveler_id")
    if named is not None and named != traveler_id:
        raise CallerRefused(
            "authorization", "The request names a different traveler than the signed-in caller."
        )
    return traveler_id, token
