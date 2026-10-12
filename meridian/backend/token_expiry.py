"""How a caller credential problem reaches the browser: 401, a Bearer challenge, a stable code.

An expired token means nothing ran, so the same request (or a workflow resume) is safe to send
again with a fresh token. The browser refreshes its token and retries, keyed on the
``token_expired`` code in the body, the ``error_description`` in ``WWW-Authenticate`` or, for the
streaming chat route, the ``code`` on the error event. A missing token is a plain 401 with the
distinct ``sign_in_required`` code: refreshing cannot help, the traveler has to sign in.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from backend.agentcore.errors import CallerCredentialError, CallerTokenExpired

TOKEN_EXPIRED_CODE = "token_expired"
CHALLENGE = f'Bearer error="invalid_token", error_description="{TOKEN_EXPIRED_CODE}"'
MESSAGE = "Your sign-in expired. Refresh it and send the request again."
SIGN_IN_REQUIRED_CODE = "sign_in_required"
SIGN_IN_MESSAGE = "Your sign-in has expired or is missing. Sign in again."


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


class SignInRequired(HTTPException):
    """A 401 for a request that carries no usable caller token."""


def sign_in_required_error() -> SignInRequired:
    """The 401 that tells the browser to send the traveler through sign-in."""
    return SignInRequired(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=SIGN_IN_MESSAGE,
        headers={"WWW-Authenticate": "Bearer"},
    )


def credential_error(error: CallerCredentialError) -> HTTPException:
    """The 401 for a caller credential failure, without echoing the exception text."""
    if isinstance(error, CallerTokenExpired):
        return token_expired_error()
    return sign_in_required_error()


def error_code(error: HTTPException) -> str | None:
    """The stable code the browser keys on, or None for any other error."""
    if is_token_expired(error):
        return TOKEN_EXPIRED_CODE
    if isinstance(error, SignInRequired):
        return SIGN_IN_REQUIRED_CODE
    return None
