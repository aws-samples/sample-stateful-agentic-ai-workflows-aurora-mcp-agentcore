"""Stop a turn when a tool could not be set up, instead of letting the model apologize.

Repeats backend/agents/strands_hooks.py, which this Runtime cannot import.
tests/test_strands_hooks.py fails if the two behave differently.

Also repeats the log masking of backend/security/redaction.py (the account pattern above,
``mask_credentials``, ``MaskingFilter`` and ``install_record_factory``) for the same reason.
``install_log_masking`` installs the record factory, so a handler a library adds later is
masked, and puts the filter on every handler of the Runtime, library loggers included.
tests/test_log_masking.py fails if the two mask differently.
"""

from __future__ import annotations

import logging
import re
import sys
import traceback
from collections.abc import Mapping
from typing import Any

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


_TOKEN_CHARS = r"[A-Za-z0-9._~+/=-]"
CREDENTIAL = re.compile(
    r"""(?<![A-Za-z])(password|secret|token|key|authorization)("""
    r"""\\?["']\s*[:=]\s*|\s*=\s*|"""
    rf"""\s*:\s*(?=["']?(?:eyJ[\w-]{{5,}}\.[\w-]{{5,}}\.|{_TOKEN_CHARS}{{16,}}(?!{_TOKEN_CHARS}))))"""
    r"""(?!["']?(?:Bearer\b|<masked>|<token>))("[^"]*"|'[^']*'|\S+)""",
    re.IGNORECASE,
)
JWT = re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
BEARER = re.compile(r"""Bearer:?\s+['"]?[A-Za-z0-9._~+/=-]{8,}""", re.IGNORECASE)


def _mask_credentials(text: str) -> str:
    """Bearer tokens, credentials and JWTs masked, as backend/security/redaction.py does."""
    bearer_masked = BEARER.sub("Bearer <token>", text)
    return JWT.sub("<token>", CREDENTIAL.sub(r"\1\2<masked>", bearer_masked))


LOG_HANDLER_MARK = "meridian_runtime_log_handler"


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


def mask_account_ids(text: str) -> str:
    """Replace each 12-digit account id in ``text`` with ``<acct>`` (as backend redaction does)."""
    return ACCOUNT_ID.sub("<acct>", text)


def setup_error_message(name: str, error: Exception) -> str:
    """Name the tool, the exception class and the masked message, cut to 300 characters."""
    detail = mask_account_ids(str(error))[:300]
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

    def __init__(self, rejections=None) -> None:
        self.message: str | None = None
        self.cause: Exception | None = None
        self.rejections = rejections

    def register_hooks(self, registry, **kwargs) -> None:
        registry.add_callback(BeforeInvocationEvent, self.reset)
        registry.add_callback(AfterToolCallEvent, self.after)
        registry.add_callback(AfterToolsEvent, self.end_turn)

    def reset(self, _event) -> None:
        self.message, self.cause = None, None

    def after(self, event) -> None:
        exception = getattr(event, "exception", None)
        if self.message is not None or not is_setup_error(exception):
            return
        self.message = setup_error_message(event.tool_use.get("name", "unknown"), exception)
        self.cause = exception

    def note_rejection(self) -> bool:
        """Stop for a Gateway rejection of a tool call; True once the turn is stopped.

        With an agent invocation open, Strands can leave a call that the Gateway rejected
        waiting forever instead of returning a result (and mcp 2.x may return one), so there is
        no stop hook for it: the Runtime polls this while it waits, and once more when the
        agent has finished.
        """
        if self.message is None and self.rejections and self.rejections.rejected:
            self.message = ("A request to the Gateway could not run (GatewayRejected): "
                            f"the Gateway answered HTTP {self.rejections.rejections[0]}.")
        return self.message is not None

    def end_turn(self, event) -> None:
        if self.cause is not None or self.message is not None:
            event.end_turn = STOPPED_REPLY

    def raise_if_stopped(self) -> None:
        """Raise the setup error that ended the last invocation, if one did.

        Raises:
            ToolSetupError: Names the tool, the exception class and the masked message, and is
                chained to the tool's own exception when it raised one.
        """
        if self.message is not None:
            raise ToolSetupError(self.message) from self.cause


def mask_log_text(text: str) -> str:
    """``text`` with credential assignments masked first, then account ids."""
    return mask_account_ids(_mask_credentials(text))


STANDARD_RECORD_ATTRIBUTES = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
FACTORY_MARK = "meridian_masking_factory"


MASKED_ARGUMENT = "<masked>"


def _map_strings(args: Any, convert: Any) -> Any:
    """``args`` with ``convert`` applied to each string in a tuple or each string mapping value."""
    if isinstance(args, tuple):
        return tuple(convert(each) if isinstance(each, str) else each for each in args)
    if isinstance(args, Mapping):
        return {key: convert(each) if isinstance(each, str) else each for key, each in args.items()}
    return args


def _rendered(template: str, args: Any) -> str | None:
    """``template % args``, or None when the template and the arguments do not fit."""
    try:
        return template % args
    except (TypeError, ValueError, KeyError):
        return None


def _mask_message(record: logging.LogRecord) -> None:
    """Mask ``record.msg`` and ``record.args`` so ``getMessage()`` and ``args`` readers both work.

    The template and each string argument are masked on their own, and ``args`` keeps its shape
    and length (uvicorn's access formatter unpacks five of them). A secret that spans the
    template and an argument (``token=%s``) is invisible to that, so when the pieces do not
    render to the masked message, the message is stored rendered and masked, with a
    ``%.0s`` per argument to consume them, and every string argument becomes ``<masked>``.
    """
    try:
        rendered = record.getMessage()
    except (TypeError, ValueError):
        record.msg, record.args = mask_log_text(f"{record.msg} {record.args!r}"), None
        return
    masked = mask_log_text(rendered)
    if not record.args:
        record.msg = masked
        return
    template = mask_log_text(record.msg) if isinstance(record.msg, str) else None
    safe = _map_strings(record.args, mask_log_text)
    if template is not None and _rendered(template, safe) == masked:
        record.msg, record.args = template, safe
        return
    consumers = "%.0s" * len(record.args) if isinstance(record.args, tuple) else ""
    record.msg = masked.replace("%", "%%") + consumers
    record.args = _map_strings(record.args, lambda _each: MASKED_ARGUMENT)


def mask_record(record: logging.LogRecord, *, detach_exception: bool = False) -> None:
    """Mask a record in place: message, traceback, stack and string ``extra=`` attributes.

    ``detach_exception`` also sets ``exc_info`` to None once ``exc_text`` holds the masked
    traceback, so a handler that renders ``exc_info`` itself finds nothing raw.
    """
    _mask_message(record)
    if record.exc_info and record.exc_info[0] is not None:
        formatted = "".join(traceback.format_exception(*record.exc_info)).rstrip("\n")
        record.exc_text = mask_log_text(formatted)
    elif record.exc_text:
        record.exc_text = mask_log_text(record.exc_text)
    if detach_exception:
        record.exc_info = None
    if record.stack_info:
        record.stack_info = mask_log_text(record.stack_info)
    for name, value in vars(record).items():
        if name not in STANDARD_RECORD_ATTRIBUTES and isinstance(value, str):
            setattr(record, name, mask_log_text(value))


def install_record_factory() -> None:
    """Mask every log record when it is created, whatever handler a library adds later.

    Wraps the factory already installed, so a library's factory still runs; installing twice
    adds one layer. ``BedrockAgentCoreApp()`` and uvicorn add their own unfiltered handlers
    after this Runtime sets up logging, and the factory runs before any of them. ``exc_info``
    becomes None once ``exc_text`` holds the masked traceback, so OpenTelemetry's logging
    handler emits the masked message and no ``exception.*`` attributes.
    """
    current = logging.getLogRecordFactory()
    if getattr(current, FACTORY_MARK, False):
        return

    def masking_factory(*args, **kwargs) -> logging.LogRecord:
        record = current(*args, **kwargs)
        mask_record(record, detach_exception=True)
        return record

    setattr(masking_factory, FACTORY_MARK, True)
    logging.setLogRecordFactory(masking_factory)


class MaskingFilter(logging.Filter):
    """Mask a log record before any formatter sees it: message, traceback, stack and extras."""

    def filter(self, record: logging.LogRecord) -> bool:
        mask_record(record)
        return True


def install_log_masking() -> None:
    """Mask every record any handler of this process writes, library loggers included.

    Strands logs each failed MCP tool call with its traceback and the botocore message. This
    installs the record factory, puts a masking handler on the root logger at WARNING (once), and
    the filter on every handler any logger already has.
    """
    install_record_factory()
    root = logging.getLogger()
    if not any(getattr(handler, LOG_HANDLER_MARK, False) for handler in root.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.WARNING)
        handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
        setattr(handler, LOG_HANDLER_MARK, True)
        root.addHandler(handler)
    loggers = [root, *(each for each in logging.Logger.manager.loggerDict.values()
                       if isinstance(each, logging.Logger))]
    for each in loggers:
        for handler in each.handlers:
            if not any(isinstance(known, MaskingFilter) for known in handler.filters):
                handler.addFilter(MaskingFilter())
