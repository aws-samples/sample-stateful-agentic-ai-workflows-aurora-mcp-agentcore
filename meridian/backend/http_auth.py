"""HTTP caller authentication for traveler-scoped Meridian APIs.

The AgentCore identity used by the backend authenticates the AWS workload. It
does not authenticate the browser or API caller. This module supplies that
separate boundary and makes the authenticated principal's traveler binding
authoritative for every traveler-scoped request.

``require_http_principal`` is the one seam every route depends on. A bearer token
is checked in this order, and a token that fails one check is never retried
against a weaker one:

1. the shared ``MERIDIAN_API_TOKEN`` (hosted release before the Cognito cutover);
2. a Cognito access token, when ``MERIDIAN_COGNITO_*`` is configured: the traveler
   is the verified ``traveler_id`` claim and nothing the caller sends can change it;
3. a direct loopback connection in development, so local scripts and tests run
   without a user pool.

The cutover release deletes the first and third paths and leaves only the second.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request, status

from backend.cognito_auth import (
    CognitoUnavailable,
    ENV_KEYS,
    CognitoVerifier,
    InvalidCognitoToken,
    get_cognito_verifier,
)
from backend.memory.store import DEMO_TRAVELER_ID

logger = logging.getLogger(__name__)

# What a browser sends when it means "the traveler I am signed in as". The server resolves it
# from the verified principal, so the client never needs to know or choose a traveler id.
CURRENT_TRAVELER = "me"


@dataclass(frozen=True)
class HttpPrincipal:
    subject_id: str
    traveler_id: str
    authentication: str


FORWARDING_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip")


def _is_loopback(request: Request) -> bool:
    """True only for a direct local connection.

    The server runs with proxy headers trusted, so ``client.host`` can be
    rewritten from ``X-Forwarded-For``. A request that went through any proxy
    is therefore never treated as local.
    """
    if any(name in request.headers for name in FORWARDING_HEADERS):
        return False
    host = request.client.host if request.client else None
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


def _local_development_allowed() -> bool:
    configured = os.getenv("MERIDIAN_ALLOW_INSECURE_LOCALHOST")
    if configured is not None:
        return configured.lower() in {"1", "true", "yes", "on"}
    return os.getenv("ENVIRONMENT", "development").lower() == "development"


def _bearer(authorization: str | None) -> str:
    """The token in an ``Authorization: Bearer`` header, or an empty string."""
    scheme, _, supplied = (authorization or "").partition(" ")
    return supplied if scheme.lower() == "bearer" else ""


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _api_traveler_id() -> str:
    return os.getenv("MERIDIAN_API_TRAVELER_ID", DEMO_TRAVELER_ID).strip()


async def _cognito_principal(verifier: CognitoVerifier, token: str) -> HttpPrincipal:
    """Verify a Cognito access token. The traveler is its verified claim."""
    try:
        identity = await asyncio.to_thread(verifier.verify, token)
    except CognitoUnavailable as exc:
        logger.error("Cognito signing keys are unavailable; no token can be verified.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sign-in verification is temporarily unavailable.",
        ) from exc
    except InvalidCognitoToken as exc:
        logger.info("Rejected a Cognito token: %s", exc.reason)
        raise _unauthorized("A valid Meridian sign-in is required.") from exc
    return HttpPrincipal(
        subject_id=identity.subject_id,
        traveler_id=identity.traveler_id,
        authentication="cognito",
    )


def _configured_verifier() -> CognitoVerifier | None:
    """The Cognito verifier, or a 503 when the pool is only half configured."""
    try:
        return get_cognito_verifier()
    except RuntimeError as exc:
        logger.error(
            "Cognito sign-in is half configured: set all of %s or none of them.",
            ", ".join(ENV_KEYS),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sign-in is misconfigured.",
        ) from exc


async def require_http_principal(
    request: Request,
    authorization: str | None = Header(default=None),
) -> HttpPrincipal:
    """Authenticate an HTTP caller and return the traveler it is bound to."""
    bearer = _bearer(authorization)
    if authorization is not None and not bearer:
        raise _unauthorized("A Bearer token is required in the Authorization header.")
    expected_token = os.getenv("MERIDIAN_API_TOKEN", "").strip()

    if expected_token and bearer and hmac.compare_digest(
        bearer.encode(), expected_token.encode()
    ):
        return HttpPrincipal(
            subject_id="configured-api-client",
            traveler_id=_api_traveler_id(),
            authentication="bearer",
        )

    verifier = _configured_verifier()
    if verifier is not None and bearer:
        return await _cognito_principal(verifier, bearer)

    if expected_token:
        raise _unauthorized("A valid Meridian bearer token is required.")

    if _local_development_allowed() and _is_loopback(request):
        return HttpPrincipal(
            subject_id="local-workshop",
            traveler_id=_api_traveler_id(),
            authentication="loopback-development",
        )

    if verifier is not None:
        raise _unauthorized("Sign in to use Meridian.")

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            "HTTP authentication is not configured. Set MERIDIAN_API_TOKEN "
            "before exposing the API beyond localhost."
        ),
    )


def authorize_traveler(
    principal: HttpPrincipal,
    requested_traveler_id: str | None,
) -> str:
    """Return the authenticated traveler or reject a caller-controlled mismatch.

    No id, an empty id and ``CURRENT_TRAVELER`` all mean the principal's own traveler.
    Any other id must equal the principal's, whatever the caller claims.
    """
    if requested_traveler_id in (None, "", CURRENT_TRAVELER):
        return principal.traveler_id
    if requested_traveler_id != principal.traveler_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The authenticated caller is not authorized for that traveler.",
        )
    return principal.traveler_id
