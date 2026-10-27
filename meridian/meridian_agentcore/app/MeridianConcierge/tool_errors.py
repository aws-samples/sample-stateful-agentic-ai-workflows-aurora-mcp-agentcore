"""Stop a turn when a tool could not be set up, instead of letting the model apologize.

Repeats backend/agents/strands_hooks.py, which this Runtime cannot import.
tests/test_strands_hooks.py fails if the two behave differently.
"""

from __future__ import annotations

import re

from botocore.exceptions import (
    BaseEndpointResolverError,
    ClientError,
    CredentialRetrievalError,
    EndpointProviderError,
    LoginError,
    NoAuthTokenError,
    NoCredentialsError,
    PartialCredentialsError,
    SSOError,
    TokenRetrievalError,
)
from strands.hooks import (
    AfterToolCallEvent,
    AfterToolsEvent,
    BeforeInvocationEvent,
    HookProvider,
)
from strands.types.exceptions import MCPClientInitializationError, ToolProviderException

SETUP_ERRORS = (
    MCPClientInitializationError,
    ToolProviderException,
    NoCredentialsError,
    PartialCredentialsError,
    CredentialRetrievalError,
    NoAuthTokenError,
    TokenRetrievalError,
    SSOError,
    LoginError,
    BaseEndpointResolverError,
    EndpointProviderError,
)
SETUP_CODES = frozenset({
    "AccessDeniedException",
    "AccessDenied",
    "UnrecognizedClientException",
    "ExpiredTokenException",
    "ExpiredToken",
    "InvalidSignatureException",
    "ResourceNotFoundException",
    "ForbiddenException",
    "HttpEndpointNotEnabledException",
    "InvalidSecretException",
    "DatabaseNotFoundException",
})
MODEL_OR_ENDPOINT_NOT_FOUND = re.compile(
    r"model identifier is invalid|\b(?:model|endpoint)\b[^.]*\bnot found\b", re.IGNORECASE)
STOPPED_REPLY = ("I could not complete that request because a service it needs is unavailable. "
                 "Please try again later.")
BOUNDARY_BEFORE = r"(?:(?<![0-9A-Za-z])|(?<=%3[Aa]|%2[Ff]))"
UUID_PREFIX = r"(?<![0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-)"
ACCOUNT_ID = re.compile(rf"{BOUNDARY_BEFORE}{UUID_PREFIX}\d{{12}}(?![0-9A-Za-z])")


class ToolSetupError(RuntimeError):
    """A tool could not be set up, so the turn cannot produce a grounded answer."""


def is_setup_error(error: object) -> bool:
    """Whether a failed tool call means the tool could not be set up, as the backend decides.

    Args:
        error: The exception the tool call raised, or None.

    Returns:
        True for a credential, permission or configuration error; False for anything else,
        such as throttling, a timeout or bad input.
    """
    if isinstance(error, SETUP_ERRORS):
        return True
    if not isinstance(error, ClientError):
        return False
    details = error.response.get("Error", {})
    code = details.get("Code")
    if code == "ValidationException":
        return bool(MODEL_OR_ENDPOINT_NOT_FOUND.search(details.get("Message") or ""))
    return code in SETUP_CODES


def setup_error_message(name: str, error: Exception) -> str:
    """Name the tool, the exception class and the masked message, cut to 300 characters."""
    detail = ACCOUNT_ID.sub("<acct>", str(error))[:300]
    return f"Tool {name} could not run ({error.__class__.__name__}): {detail}"


class StopOnToolSetupError(HookProvider):
    """End the turn after a tool that could not be set up, without calling the model again.

    The Strands tool executor catches what an ``AfterToolCallEvent`` callback raises, so the
    hook records the first setup error and sets ``AfterToolsEvent.end_turn`` instead. "First"
    means the first to finish: on the default ``ConcurrentToolExecutor`` that need not be the
    first tool the model requested, while on ``SequentialToolExecutor`` it is. The turn ends on
    ``STOPPED_REPLY``, which AgentCore Memory keeps, so it carries no detail. ``run`` then calls
    ``raise_if_stopped``, which raises the detailed error.
    """

    def __init__(self) -> None:
        self.message: str | None = None
        self.cause: Exception | None = None

    def register_hooks(self, registry, **kwargs) -> None:
        registry.add_callback(BeforeInvocationEvent, self.reset)
        registry.add_callback(AfterToolCallEvent, self.after)
        registry.add_callback(AfterToolsEvent, self.end_turn)

    def reset(self, _event) -> None:
        self.message, self.cause = None, None

    def after(self, event) -> None:
        exception = getattr(event, "exception", None)
        if self.cause is not None or not is_setup_error(exception):
            return
        self.message = setup_error_message(event.tool_use.get("name", "unknown"), exception)
        self.cause = exception

    def end_turn(self, event) -> None:
        if self.cause is not None:
            event.end_turn = STOPPED_REPLY

    def raise_if_stopped(self) -> None:
        """Raise the setup error that ended the last invocation, if one did.

        Raises:
            ToolSetupError: Names the tool, the exception class and the masked message, and is
                chained to the tool's own exception.
        """
        if self.cause is not None:
            raise ToolSetupError(self.message) from self.cause
