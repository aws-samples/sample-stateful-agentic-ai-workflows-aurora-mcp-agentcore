"""Where the signed-in caller's access token travels inside one process.

The backend verifies the token once, in ``require_http_principal``. The Runtime clients and the
Gateway client deep below the routes need the same raw token to forward it, and threading it through
every function signature would touch dozens of call sites. The token therefore lives in a
per-request holder:

- ``CallerCredentialMiddleware`` puts an empty holder in a context variable before each request.
- ``bind_caller_token`` fills the holder. Tasks and threads started later copy the context and
  share the holder, so they see the token wherever it was bound.
- ``current_caller_token`` reads it.

Inside an AgentCore Runtime nothing runs the middleware, so the entry point calls
``bind_caller_token`` itself or wraps its work in ``caller_token_scope``. ``bind_caller_token``
needs a middleware or a scope around it: with neither it creates a holder that nothing ever
resets, so the token outlives the call that bound it (the test suite wraps every test in a scope).

Tasks and ``asyncio.to_thread`` copy the context and see the holder. ``loop.run_in_executor`` does
not copy it, so work started that way sees no token and fails closed. When a request ends the
middleware empties its holder, so a task that outlives the request also sees none.

This module is stdlib only because the MeridianWorkflow Runtime bundles it. It never logs, prints
or formats a token.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Any, Awaitable, Callable, Iterator, Mapping, Optional

from backend.agentcore.errors import CallerTokenMissing


class _Holder:
    """A mutable cell so a token bound after a task was spawned is still seen by that task."""

    __slots__ = ("token",)

    def __init__(self, token: Optional[str] = None) -> None:
        self.token = token


_HOLDER: contextvars.ContextVar[Optional[_Holder]] = contextvars.ContextVar(
    "meridian_caller_credential", default=None
)


def bearer_from_headers(headers: Optional[Mapping[str, Any]]) -> Optional[str]:
    """The token in an ``Authorization: Bearer`` header, or None.

    The header name is matched case-insensitively. Returns None when the header is absent, uses
    another scheme, or carries no token.
    """
    for name, value in (headers or {}).items():
        if isinstance(name, str) and name.lower() == "authorization" and isinstance(value, str):
            scheme, _, token = value.strip().partition(" ")
            if scheme.lower() == "bearer" and token.strip():
                return token.strip()
    return None


def bind_caller_token(token: Optional[str]) -> None:
    """Record the verified caller's token for everything running under this request.

    Call it inside ``CallerCredentialMiddleware`` or ``caller_token_scope``; outside both the
    binding is never reset.
    """
    holder = _HOLDER.get()
    if holder is None:
        holder = _Holder()
        _HOLDER.set(holder)
    holder.token = token


def current_caller_token() -> Optional[str]:
    """The bound token, or None when no request has bound one."""
    holder = _HOLDER.get()
    return holder.token if holder else None


def require_caller_token() -> str:
    """The bound token.

    Raises:
        CallerTokenMissing: No token is bound, so a bearer-mode call cannot be made.
    """
    token = current_caller_token()
    if not token:
        raise CallerTokenMissing(
            "No caller token is bound. In jwt mode every AgentCore call needs the signed-in "
            "person's access token; scripts bind one with caller_token_scope()."
        )
    return token


@contextmanager
def caller_token_scope(token: Optional[str]) -> Iterator[None]:
    """Run a block, for example a script, with ``token`` as the caller's token."""
    reset = _HOLDER.set(_Holder(token))
    try:
        yield
    finally:
        _HOLDER.reset(reset)


class CallerCredentialMiddleware:
    """Pure ASGI middleware that gives every request its own empty holder."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        holder = _Holder()
        reset = _HOLDER.set(holder)
        try:
            await self.app(scope, receive, send)
        finally:
            holder.token = None
            _HOLDER.reset(reset)
