"""Strands hooks shared by the local agents."""

import re
from typing import Optional, Sequence

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
from strands.tools.mcp.mcp_client import CLIENT_SESSION_NOT_RUNNING_ERROR_MESSAGE
from strands.types.exceptions import MCPClientInitializationError, ToolProviderException

from backend.security.redaction import mask_account_ids

# A tool that never ran: the Gateway or its tool list could not be reached, botocore found no
# usable credentials, or it could not resolve an endpoint (NoRegionError and
# EndpointResolutionError are under the two endpoint bases). Retrying the call cannot help.
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
# AWS error codes for a credential, permission or configuration problem. Throttling, timeouts and
# an unavailable service are left out: the model, or the Phase 3 search re-run, can recover.
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
# A ValidationException is a setup error only in its not-found form for a model or an endpoint.
# Amazon Bedrock words an unknown model "The provided model identifier is invalid".
MODEL_OR_ENDPOINT_NOT_FOUND = re.compile(
    r"model identifier is invalid|\b(?:model|endpoint)\b[^.]*\bnot found\b", re.IGNORECASE)
# What Strands writes for an MCP client that is not running or never initialized. It builds these
# from an ``MCPClientInitializationError`` and returns them as a result, so no exception reaches
# the hooks.
CLIENT_SETUP_TEXTS = (
    CLIENT_SESSION_NOT_RUNNING_ERROR_MESSAGE,
    "the client initialization failed",
    "the client session was not initialized",
)
# A botocore error as an MCP server or client reports it in an error result. Anchored after the
# FastMCP or Strands prefix, so text the model sent as a tool argument cannot match.
MCP_BOTO_ERROR = re.compile(
    r"(?:Error executing tool \S+|Tool execution failed): "
    r"An error occurred \((?P<code>\w+)\) when calling the \w+ operation[^:]*: (?P<message>.*)",
    re.DOTALL)
CLIENT_ERROR_PREFIX = "Tool execution failed: "
# The assistant's reply for a stopped turn. Memory keeps it, so it names no tool, class or error.
STOPPED_REPLY = ("I could not complete that request because a service it needs is unavailable. "
                 "Please try again later.")


class ToolSetupError(RuntimeError):
    """A tool could not be set up, so the turn cannot produce a grounded answer."""


def is_setup_code(code: Optional[str], message: Optional[str]) -> bool:
    """Whether an AWS error code, with its message, is a credential, permission or setup error."""
    if code == "ValidationException":
        return bool(MODEL_OR_ENDPOINT_NOT_FOUND.search(message or ""))
    return code in SETUP_CODES


def is_setup_error(error: object) -> bool:
    """Whether a failed tool call means the tool could not be set up.

    Args:
        error: The exception the tool call raised, or None.

    Returns:
        True for a setup class in ``SETUP_ERRORS``, or a ``ClientError`` whose code is in
        ``SETUP_CODES`` or is a ValidationException for a model or endpoint that is not found.
        False for anything else, such as throttling, a timeout or bad input.
    """
    if isinstance(error, SETUP_ERRORS):
        return True
    if not isinstance(error, ClientError):
        return False
    details = error.response.get("Error", {})
    return is_setup_code(details.get("Code"), details.get("Message"))


def is_setup_error_text(text: str) -> bool:
    """Whether the text of an MCP error result says its tool could not be set up.

    Strands never raises for an MCP tool. A server-side failure arrives as
    ``Error executing tool <name>: An error occurred (<Code>) when calling ...`` and a
    client-side one as ``Tool execution failed: ...``. Both count when the code is one
    ``is_setup_error`` treats as setup-class, and so does a client-built text that Strands wrote
    for an ``MCPClientInitializationError`` (a session that is not running).

    Args:
        text: The text blocks of an error result, joined.

    Returns:
        True for a setup-class code, or a client-built initialization error text.
    """
    found = MCP_BOTO_ERROR.match(text)
    if found:
        return is_setup_code(found["code"], found["message"])
    if text.startswith(CLIENT_ERROR_PREFIX):
        detail = text[len(CLIENT_ERROR_PREFIX):]
        return any(detail.startswith(known) for known in CLIENT_SETUP_TEXTS)
    return False


def setup_error_message(name: str, error: Exception) -> str:
    """Name the tool, the exception class and the masked message, cut to 300 characters."""
    detail = mask_account_ids(str(error))[:300]
    return f"Tool {name} could not run ({error.__class__.__name__}): {detail}"


class StopOnToolSetupError(HookProvider):
    """End the turn after a tool that could not be set up, without calling the model again.

    Without it the model sees the failed call as a tool result and answers with a generic
    decline. A hook cannot end the turn by raising: the Strands tool executor catches what an
    ``AfterToolCallEvent`` callback raises and returns it to the model as one more failed call.
    So the hook records the first setup error and sets ``AfterToolsEvent.end_turn``. "First"
    means the first to finish: on the default ``ConcurrentToolExecutor`` that need not be the
    first tool the model requested, while on ``SequentialToolExecutor`` it is. The event loop
    then appends ``STOPPED_REPLY`` as the assistant's reply and stops without another model
    call. That reply stays in the conversation, so it carries no detail. The caller that ran the
    agent calls ``raise_if_stopped``, and the turn fails with the detailed error.

    An MCP tool never raises, so its setup failure is not an exception. ``mcp_failures`` is the
    list a ``RetrievalTraceHooks`` fills (it classifies each error result once); when the batch of
    tool calls ends, the first failure marked ``setup`` stops the turn the same way.
    """

    def __init__(self, mcp_failures: Sequence = ()) -> None:
        self.message: Optional[str] = None
        self.cause: Optional[Exception] = None
        self.mcp_failures = mcp_failures
        self._failures_before_invocation = 0

    def register_hooks(self, registry, **kwargs) -> None:
        registry.add_callback(BeforeInvocationEvent, self.reset)
        registry.add_callback(AfterToolCallEvent, self.after)
        registry.add_callback(AfterToolsEvent, self.end_turn)

    def reset(self, _event) -> None:
        """Forget the last invocation's setup error and the MCP failures recorded before it.

        The list belongs to the trace hooks, which read all of it after the turn, so it is not
        cleared; only the entries added from now on can stop this invocation.
        """
        self.message, self.cause = None, None
        self._failures_before_invocation = len(self.mcp_failures)

    def after(self, event) -> None:
        exception = getattr(event, "exception", None)
        if self.cause is not None or not is_setup_error(exception):
            return
        self.message = setup_error_message(event.tool_use.get("name", "unknown"), exception)
        self.cause = exception

    def end_turn(self, event) -> None:
        if self.message is None:
            new = self.mcp_failures[self._failures_before_invocation:]
            failed = next((f for f in new if f.setup), None)
            if failed is not None:
                self.message = (f"Tool {failed.tool} could not run (MCP {failed.origin} error): "
                                f"{failed.detail}")
        if self.message is not None:
            event.end_turn = STOPPED_REPLY

    def raise_if_stopped(self) -> None:
        """Raise the setup error that ended the last invocation, if one did.

        Raises:
            ToolSetupError: Names the tool, the exception class (or, for an MCP result, its
                origin) and the masked message; chained to the tool's own exception when it
                raised one.
        """
        if self.message is not None:
            raise ToolSetupError(self.message) from self.cause
