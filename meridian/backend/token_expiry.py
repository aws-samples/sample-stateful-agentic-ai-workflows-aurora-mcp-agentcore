"""How an expired caller token reaches the browser: 401, a Bearer challenge, a stable code.

An expired token means nothing ran, so the same request (or a workflow resume) is safe to send
again with a fresh token. The browser refreshes its token and retries, keyed on the
``token_expired`` code in the body, the ``error_description`` in ``WWW-Authenticate`` or, for the
streaming chat route, the ``code`` on the error event.
"""

from __future__ import annotations

from fastapi import HTTPException, status

TOKEN_EXPIRED_CODE = "token_expired"
CHALLENGE = f'Bearer error="invalid_token", error_description="{TOKEN_EXPIRED_CODE}"'
MESSAGE = "Your sign-in expired. Refresh it and send the request again."


def token_expired_error() -> HTTPException:
    """The 401 that tells the browser to refresh its token and retry."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=MESSAGE,
        headers={"WWW-Authenticate": CHALLENGE},
    )


def is_token_expired(error: HTTPException) -> bool:
    """True for an exception built by :func:`token_expired_error`."""
    return (error.headers or {}).get("WWW-Authenticate") == CHALLENGE
