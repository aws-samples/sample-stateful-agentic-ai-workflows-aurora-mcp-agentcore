"""Mask AWS account ids in the error text the backend stores or shows and in the identity
fields it returns.

An account id is a run of exactly 12 digits that no letter or digit touches. Hyphens,
colons, slashes, quotes and spaces bound it, and so does a percent-encoded colon or
slash before it (``%3A``, ``%3a``, ``%2F``, ``%2f``), as in the Runtime ARN of a
botocore request URL: ``arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A<acct>%3Aruntime``.
The last group of a canonical UUID is never masked. Each id becomes ``<acct>``.

Identity columns such as ``agent_audit_log.iam_identity`` keep the full ARN so an
investigation can still use it; the mask is applied when that text leaves the backend:
the System evidence Authorization tab, the journey document, the Phase 4 trace fields
and the RLS probe response. Error text is masked before it is stored or shown:
``agent_audit_log.turn_error``, tool setup errors and retrieval failure notes. The Concierge
Runtime repeats the pattern in ``tool_errors.py``.

Text written to the backend log is also stripped of ``password=``, ``secret=``, ``token=``
and ``key=`` assignments (``mask_credentials``), because an exception text can echo a
connection string. ``install_record_factory`` applies both masks to every record when it is
created, whoever logged it and whichever handler a library adds later, so a library logger's
traceback is masked too. ``MaskingFilter`` repeats the masks on each Meridian handler and
covers the ``extra=`` attributes the factory cannot see.
"""

import logging
import re
import traceback
from collections.abc import Mapping
from typing import Any

# An account id is a 12-digit run that no letter or digit touches, so hyphens, colons,
# slashes and quotes bound it (``fn-123456789012`` is masked). A percent-encoded colon or
# slash (``%3A``, ``%3a``, ``%2F``, ``%2f``) bounds it too, although it ends in a letter:
# botocore names the request URL in a transport error, and that URL holds the Runtime ARN
# percent-encoded. After the run, the ``%`` already bounds it. The final group of a
# canonical UUID is also 12 hex characters and can be all digits; the lookbehind
# keeps it, because span, thread and journey ids are UUIDs.
_BOUNDARY_BEFORE = r"(?:(?<![0-9A-Za-z])|(?<=%3[Aa]|%2[Ff]))"
_UUID_PREFIX = r"(?<![0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-)"
ACCOUNT_ID_PATTERN = re.compile(rf"{_BOUNDARY_BEFORE}{_UUID_PREFIX}\d{{12}}(?![0-9A-Za-z])")
PLACEHOLDER = "<acct>"
CREDENTIAL_PATTERN = re.compile(r"(password|secret|token|key)=\S+", re.IGNORECASE)
# A token of at least 8 characters, optionally quoted. The 8-character floor keeps the fixed
# text "no bearer token was forwarded" readable; a real token (a JWT, an opaque key) is longer.
# Prose such as "bearer authentication failed" is masked too, because exempting words would let
# an alphabetic token through.
BEARER_PATTERN = re.compile(r"""Bearer\s+['"]?[A-Za-z0-9._~+/=-]{8,}""", re.IGNORECASE)
BEARER_PLACEHOLDER = "Bearer <token>"
MAX_TRACEBACK_CHARS = 4000
_CUT = "\n...\n"


def mask_account_ids(text: str) -> str:
    """Replace each free-standing run of exactly 12 digits with ``<acct>``.

    Args:
        text: Any text, such as an IAM role ARN.

    Returns:
        The text with every 12-digit run masked unless a letter or digit touches
        it or it is the last group of a canonical UUID. A percent-encoded colon or
        slash before the run (``%3A``, ``%2F``, in either case) bounds it like the
        character it encodes. Masking a masked string changes nothing.
    """
    return ACCOUNT_ID_PATTERN.sub(PLACEHOLDER, text)


def mask_credentials(text: str) -> str:
    """Replace the value of ``password=``, ``secret=``, ``token=`` and ``key=`` with ``<masked>``.

    The MeridianCatalog Lambda (``meridian_agentcore/agentcore/gateway_targets/meridian_catalog/
    lambda_function.py``) ships as one file and cannot import the backend, so it repeats this
    exact pattern; keep the two in step.

    Args:
        text: Any text, such as an exception message.

    Returns:
        The text with each match's value (up to the next whitespace) replaced. The name is
        kept, case included. Masking a masked string changes nothing.
    """
    return CREDENTIAL_PATTERN.sub(r"\1=<masked>", text)


def mask_secrets(text: str) -> str:
    """``text`` with bearer tokens, credential assignments and account ids masked, uncut.

    Stored text (an audit row's ``turn_error``) and operator logs use this one function. An ARN
    keeps its partition, service and resource name, as the backend log keeps them; only the
    account id in it is masked. Each caller applies its own length limit.
    """
    return mask_account_ids(mask_credentials(BEARER_PATTERN.sub(BEARER_PLACEHOLDER, text)))


def mask_log_text(text: str) -> str:
    """``text`` with credentials masked first, then account ids, for the backend log."""
    return mask_account_ids(mask_credentials(text))


def masked_traceback(error: BaseException, limit: int = MAX_TRACEBACK_CHARS) -> str:
    """The traceback of ``error`` as log text, with credentials and account ids masked.

    A traceback names the exception text, and that text can carry an account id (a Data API
    error names the role ARN), and a credential assignment (``password=...``). The masks run
    before the cut, so a cut never leaves half a credential. Log this instead of passing
    ``exc_info`` to the logger.

    Args:
        error: The exception, with the ``__traceback__`` it was raised with.
        limit: Most characters to return. Longer text keeps its first quarter, where
            ``Traceback`` and the outer frames are, and its end, where the exception class,
            its text and the innermost frames are.

    Returns:
        The formatted traceback, masked, and cut to ``limit`` characters with ``\n...\n``
        between the two kept parts when it was longer.
    """
    text = mask_log_text("".join(traceback.format_exception(error)))
    if len(text) <= limit:
        return text
    head = limit // 4
    return text[:head] + _CUT + text[len(text) - (limit - head - len(_CUT)):]


_STANDARD_RECORD_ATTRIBUTES = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
_FACTORY_MARK = "meridian_masking_factory"


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
    """Mask ``record`` in place: message, traceback, stack and string ``extra=`` attributes.

    The message and its arguments are masked in place and ``args`` keeps its shape, so a
    formatter that unpacks it still works (``_mask_message``). A set ``exc_info`` is
    formatted (chain included) into a masked ``exc_text``, which formatters reuse.
    ``detach_exception`` also sets ``exc_info`` to None, so a handler that renders
    ``exc_info`` itself (a Rich handler) finds nothing raw to render. Masking twice changes
    nothing.

    Args:
        record: The record to mask.
        detach_exception: Drop ``exc_info`` once ``exc_text`` holds the masked traceback.
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
        if name not in _STANDARD_RECORD_ATTRIBUTES and isinstance(value, str):
            setattr(record, name, mask_log_text(value))


def install_record_factory() -> None:
    """Mask every log record when it is created, whatever handler later writes it.

    A filter protects only the handler it sits on, and libraries add handlers after an
    application sets up logging (the AgentCore app adds one to ``bedrock_agentcore.app``,
    uvicorn adds its own at ``run``). A record factory runs before any handler, so none of
    them sees an unmasked message or traceback, and a record logged with no handler at all
    (``logging.lastResort``) is masked too. The factory wraps the one already installed, so a
    library's factory still runs, and installing twice adds one layer. ``extra=`` attributes
    are set after the factory runs; the handler filters mask those.

    The factory sets ``exc_info`` to None once ``exc_text`` holds the masked traceback, whose
    last line is the exception class and its masked message. OpenTelemetry's logging handler
    reads ``exception.type``, ``exception.message`` and ``exception.stacktrace`` from
    ``exc_info`` only, so it now emits the masked message and no ``exception.*`` attributes.
    """
    current = logging.getLogRecordFactory()
    if getattr(current, _FACTORY_MARK, False):
        return

    def masking_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = current(*args, **kwargs)
        mask_record(record, detach_exception=True)
        return record

    setattr(masking_factory, _FACTORY_MARK, True)
    logging.setLogRecordFactory(masking_factory)


class MaskingFilter(logging.Filter):
    """Mask a log record before any formatter sees it.

    Put it on every handler: it masks ``extra=`` attributes, which are set after the record
    factory (``install_record_factory``) ran, and a record created before the factory existed.
    It replaces the message with its masked, argument-substituted form, sets ``exc_text`` from
    the masked traceback (formatters reuse a set ``exc_text``) and masks ``stack_info``. The
    Concierge Runtime repeats this class in ``tool_errors.py`` because it cannot import the
    backend; keep the two in step.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Mask ``record`` in place; always keeps the record."""
        mask_record(record)
        return True


def install_masking(logger: logging.Logger | None = None) -> None:
    """Add one ``MaskingFilter`` to each handler of ``logger`` (the root logger by default)."""
    install_record_factory()
    for handler in (logger or logging.getLogger()).handlers:
        if not any(isinstance(each, MaskingFilter) for each in handler.filters):
            handler.addFilter(MaskingFilter())


def mask_payload(value: Any) -> Any:
    """Return a deep copy of ``value`` with every string masked.

    Args:
        value: A string, or dicts, lists and tuples nested to any depth. Dict keys
            that are strings are masked too.

    Returns:
        A new structure. Non-string scalars come back unchanged.
    """
    if isinstance(value, str):
        return mask_account_ids(value)
    if isinstance(value, dict):
        return {mask_payload(key): mask_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [mask_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(mask_payload(item) for item in value)
    return value
