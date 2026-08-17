"""HTTP caller authentication for traveler-scoped Meridian APIs.

The AgentCore identity used by the backend authenticates the AWS workload. It
does not authenticate the browser or API caller. This module supplies that
separate boundary and makes the authenticated principal's traveler binding
authoritative for every traveler-scoped request.
"""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request, status

from backend.memory.store import DEMO_TRAVELER_ID


@dataclass(frozen=True)
class HttpPrincipal:
    subject_id: str
    traveler_id: str
    authentication: str


def _is_loopback(host: str | None) -> bool:
    return host in {"127.0.0.1", "::1", "localhost", "testclient"}


def _local_development_allowed() -> bool:
    configured = os.getenv("MERIDIAN_ALLOW_INSECURE_LOCALHOST")
    if configured is not None:
        return configured.lower() in {"1", "true", "yes", "on"}
    return os.getenv("ENVIRONMENT", "development").lower() == "development"


async def require_http_principal(
    request: Request,
    authorization: str | None = Header(default=None),
) -> HttpPrincipal:
    """Authenticate an HTTP caller and return its fixed traveler binding."""
    traveler_id = os.getenv("MERIDIAN_API_TRAVELER_ID", DEMO_TRAVELER_ID).strip()
    expected_token = os.getenv("MERIDIAN_API_TOKEN", "").strip()

    if expected_token:
        scheme, _, supplied = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not supplied or not hmac.compare_digest(
            supplied, expected_token
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="A valid Meridian bearer token is required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return HttpPrincipal(
            subject_id="configured-api-client",
            traveler_id=traveler_id,
            authentication="bearer",
        )

    client_host = request.client.host if request.client else None
    if _local_development_allowed() and _is_loopback(client_host):
        return HttpPrincipal(
            subject_id="local-workshop",
            traveler_id=traveler_id,
            authentication="loopback-development",
        )

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
    """Return the authenticated traveler or reject a caller-controlled mismatch."""
    if requested_traveler_id and requested_traveler_id != principal.traveler_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The authenticated caller is not authorized for that traveler.",
        )
    return principal.traveler_id
