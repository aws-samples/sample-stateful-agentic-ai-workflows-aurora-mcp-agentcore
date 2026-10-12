"""Verification of Amazon Cognito access tokens for the Meridian API.

The user pool is the only source of the traveler identity. A pre-token-generation trigger
copies ``traveler_id`` from Aurora's ``traveler_identity_bindings`` into the access token, so
the claim is trustworthy only after the signature, issuer, token type, app client and expiry
have all been verified. This module does that and nothing else: it knows no HTTP framework.
``backend.http_auth`` turns a ``VerifiedIdentity`` into the request's principal.

Configuration (all three or none):

    MERIDIAN_COGNITO_REGION=us-east-1
    MERIDIAN_COGNITO_USER_POOL_ID=us-east-1_AbCdEfGhI
    MERIDIAN_COGNITO_APP_CLIENT_ID=<the web app client's id>

Logging: log only ``InvalidCognitoToken.reason``. Never log an exception's message or
traceback from this module: ``PyJWKClientError`` messages embed the attacker-supplied ``kid``,
and the raised exceptions are chained to them.

AWS docs:
  - Verifying a JSON web token:
    https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-tokens-verifying-a-jwt.html
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable, Optional

import jwt
from jwt import PyJWKClient
from jwt.exceptions import (
    DecodeError,
    ExpiredSignatureError,
    ImmatureSignatureError,
    InvalidAlgorithmError,
    InvalidIssuerError,
    InvalidSignatureError,
    InvalidSubjectError,
    MissingRequiredClaimError,
    PyJWKClientConnectionError,
    PyJWKClientError,
    PyJWKSetError,
    PyJWTError,
)

TRAVELER_CLAIM = "traveler_id"
TRAVELER_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,50}$")
ALGORITHMS = ["RS256"]
JWKS_LIFESPAN_SECONDS = 3600
JWKS_TIMEOUT_SECONDS = 5
JWKS_REFETCH_COOLDOWN_SECONDS = 30
UNAVAILABLE_BACKOFF_SECONDS = 10
CLOCK_SKEW_SECONDS = 30
REQUIRED_CLAIMS = ["exp", "iss", "sub", "token_use"]
ENV_KEYS = (
    "MERIDIAN_COGNITO_REGION",
    "MERIDIAN_COGNITO_USER_POOL_ID",
    "MERIDIAN_COGNITO_APP_CLIENT_ID",
)

_REASONS = (
    (ExpiredSignatureError, "expired"),
    (InvalidIssuerError, "issuer"),
    (InvalidSignatureError, "signature"),
    (InvalidAlgorithmError, "algorithm"),
    (MissingRequiredClaimError, "missing_claim"),
    (InvalidSubjectError, "missing_claim"),
    (ImmatureSignatureError, "not_yet_valid"),
    (PyJWKClientError, "signing_key"),
    (PyJWKSetError, "signing_key"),
    (DecodeError, "malformed"),
)

SigningKeyProvider = Callable[[str], Any]


class InvalidCognitoToken(Exception):
    """A bearer token failed verification.

    Attributes:
        reason: A short code that is safe to log. It never contains the token.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CognitoUnavailable(Exception):
    """The signing keys could not be fetched, so no token can be verified right now."""


class _GuardedJWKClient(PyJWKClient):
    """A ``PyJWKClient`` that does not hammer a JWKS endpoint that just failed.

    ``fetch_data`` runs inside the client's own lock, so concurrent callers queue behind one
    failed attempt and then see the backoff window instead of each waiting out a timeout.
    Cache hits never reach ``fetch_data`` and are unaffected.
    """

    _down_until = 0.0

    def fetch_data(self) -> Any:
        if time.monotonic() < self._down_until:
            raise PyJWKClientConnectionError("JWKS unavailable; not retrying yet")
        try:
            return super().fetch_data()
        except (PyJWKClientConnectionError, OSError, ValueError) as exc:
            self._down_until = time.monotonic() + UNAVAILABLE_BACKOFF_SECONDS
            raise PyJWKClientConnectionError("JWKS fetch failed") from exc


@dataclass(frozen=True)
class CognitoConfig:
    """The one user pool and app client the API accepts."""

    region: str
    user_pool_id: str
    app_client_id: str

    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}"

    @property
    def jwks_url(self) -> str:
        return f"{self.issuer}/.well-known/jwks.json"


@dataclass(frozen=True)
class VerifiedIdentity:
    """What a verified access token proves.

    Attributes:
        subject_id: The Cognito ``sub``, stable for the life of the user.
        traveler_id: The verified ``traveler_id`` claim.
        username: The token's ``username`` claim, when present.
    """

    subject_id: str
    traveler_id: str
    username: Optional[str]


def cognito_config_from_env() -> Optional[CognitoConfig]:
    """Read the Cognito settings, or None when none are set.

    Raises:
        RuntimeError: When only some of the three settings are set, because a half-configured
            verifier would silently fall back to weaker authentication.
    """
    values = {key: os.getenv(key, "").strip() for key in ENV_KEYS}
    if not any(values.values()):
        return None
    missing = [key for key, value in values.items() if not value]
    if missing:
        raise RuntimeError(
            f"Cognito sign-in is half configured: set {', '.join(missing)} or unset the others."
        )
    return CognitoConfig(*(values[key] for key in ENV_KEYS))


class CognitoVerifier:
    """Verifies Cognito access tokens against one user pool and one app client.

    Only ``InvalidCognitoToken.reason`` is safe to log; see the module docstring.

    After a JWKS fetch fails, further fetches are refused for ``UNAVAILABLE_BACKOFF_SECONDS``
    inside PyJWT's client lock, so a down endpoint costs one timeout per window rather than one
    per request. Tokens whose key is already cached keep verifying.

    Args:
        config: The pool and client to accept.
        signing_key_for: Returns the public key that signed a token. The default fetches the
            pool's JWKS and caches the key set for ``JWKS_LIFESPAN_SECONDS``; tests inject a
            local key.
    """

    def __init__(
        self, config: CognitoConfig, signing_key_for: Optional[SigningKeyProvider] = None
    ) -> None:
        self._config = config
        if signing_key_for is None:
            self._jwks_client = _GuardedJWKClient(
                config.jwks_url,
                lifespan=JWKS_LIFESPAN_SECONDS,
                timeout=JWKS_TIMEOUT_SECONDS,
                cooldown_duration=JWKS_REFETCH_COOLDOWN_SECONDS,
            )
            signing_key_for = self._key_from_jwks
        self._signing_key_for = signing_key_for

    def _key_from_jwks(self, token: str) -> Any:
        return self._jwks_client.get_signing_key_from_jwt(token).key

    def verify(self, token: str) -> VerifiedIdentity:
        """Verify an access token and return the identity it proves.

        Raises:
            InvalidCognitoToken: The signature, issuer, expiry, token type, app client,
                subject or traveler claim is wrong.
            CognitoUnavailable: The signing keys could not be fetched.
        """
        if not token.isascii():
            raise InvalidCognitoToken("malformed")
        claims = self._decode(token)
        if claims.get("token_use") != "access":
            raise InvalidCognitoToken("token_use")
        if claims.get("client_id") != self._config.app_client_id:
            raise InvalidCognitoToken("client_id")
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise InvalidCognitoToken("missing_claim")
        traveler_id = claims.get(TRAVELER_CLAIM)
        if not isinstance(traveler_id, str) or not TRAVELER_ID_PATTERN.fullmatch(traveler_id):
            raise InvalidCognitoToken("traveler_claim")
        username = claims.get("username")
        return VerifiedIdentity(
            subject_id=subject,
            traveler_id=traveler_id,
            username=username if isinstance(username, str) else None,
        )

    def _decode(self, token: str) -> dict:
        key = self._lookup_key(token)
        try:
            return jwt.decode(
                token,
                key=key,
                algorithms=ALGORITHMS,
                issuer=self._config.issuer,
                leeway=CLOCK_SKEW_SECONDS,
                options={"require": REQUIRED_CLAIMS, "verify_aud": False},
            )
        except PyJWTError as exc:
            raise InvalidCognitoToken(_reason(exc)) from exc

    def _lookup_key(self, token: str) -> Any:
        try:
            return self._signing_key_for(token)
        except PyJWKClientConnectionError as exc:
            raise CognitoUnavailable("The Cognito signing keys could not be fetched.") from exc
        except PyJWTError as exc:
            raise InvalidCognitoToken(_reason(exc)) from exc


def _reason(error: Exception) -> str:
    for kind, reason in _REASONS:
        if isinstance(error, kind):
            return reason
    return "invalid"


@lru_cache(maxsize=4)
def _verifier_for(config: CognitoConfig) -> CognitoVerifier:
    return CognitoVerifier(config)


def get_cognito_verifier() -> Optional[CognitoVerifier]:
    """The verifier for the configured pool, or None when Cognito sign-in is not configured.

    The verifier, and the signing keys it caches, are shared by every request for a given
    configuration.
    """
    config = cognito_config_from_env()
    return _verifier_for(config) if config else None
