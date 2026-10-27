"""Each tool call a Phase 4 turn made and how it ended, read from the spans the Runtime reported.

The Runtime marks every span of one tool call with the call's ``call_id`` (the Strands
``toolUseId``): the span before the call ran and the span with its result. The tool is named by
the span before it ran, and calls are listed in the order those spans arrived, which is the order
the calls started. Calls that ran in parallel report their results in completion order, so a
Runtime whose spans carry no ``call_id`` cannot say which result belongs to which call. Its turns
record the calls as unknown (None), never as a guessed list. So does a turn with a span that
cannot be read, since that span may have been a call's.

A refusal names a layer only from that layer's own evidence: Cedar's denial; the request
interceptor's ``Identity Check Failed:`` text, with its reason key as the code; the holds Lambda's
``traveler_not_authorized`` together with its grant decision and the ``traveler_access_audit``
deny row it kept; a named code the Lambda or an Aurora function raised, classified in
``backend.audit_vocabulary.CODE_CLASSES``; or the Runtime's own span for a second governed write
it declined to send. Any other failure is an ``error`` with no layer, keeping a named code when
the evidence carries one and never storing free text as a code.

The trace shows the same reading: ``read_turn`` marks the span that settled each call the row
lists as refused by a layer other than Cedar, so the trace and the row cannot disagree.

A turn whose Runtime call fails is read the same way from the spans the Runtime reported before
its error event (``audit_of_failed_turn``); a call that fails with nothing reported records the
calls as unknown. A turn that fails after the Runtime returned, before its row is written,
keeps the calls read from its decision. Each of these rows names the error (``turn_error_of``),
so it is never ``allow``. ``turn_error`` stays NULL only for a turn that completed, or one that
failed after its row was committed (the memory step and the trace entries that follow it).
"""

import re
from dataclasses import dataclass, replace
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from backend.agentcore.runtime import RuntimeDecision, RuntimeTurnError
from backend.audit_vocabulary import (
    ALLOWED,
    BUSINESS_RULE,
    CODE_CLASSES,
    DATABASE_GUARD,
    ERROR,
    IDENTITY_CHECK,
    IDENTITY_REFUSAL,
    POLICY,
    REFUSED,
    RUNTIME_GUARD,
    WORKLOAD_GRANT,
    WORKLOAD_GRANT_REFUSAL,
    ToolCall,
    authorization_decision,
    identity_refusal_code,
)
from backend.security.redaction import mask_account_ids
from backend.tools.tool_names import bare_tool_name

# The labels only a tool call's spans carry; the Runtime's start, tool list and memory spans
# carry none of them.
CALL_LABELS = ("call_id", "tool", "cedar_decision")
# The labels whose text a row stores in ``tool_calls``. Text jsonb cannot store in one of them
# makes the calls unknown, because replacing a character would invent a tool name or an id.
STORED_LABELS = ("tool", "refusal_code", "access_audit_id")
LONE_SURROGATE = re.compile("[\ud800-\udfff]")
# The trace's status for a call a layer other than Cedar refused. A Cedar denial keeps the
# Runtime's own ``denied``.
REFUSED_STATUS = "refused"
# How a refused span's title names a governed write; any other tool goes by its contract name.
WRITE_NOUNS = {"create_courtesy_hold": "Hold", "confirm_booking": "Booking"}
Verdict = Tuple[str, Optional[str], Optional[str], Optional[str]]
# One call span: its position among the turn's spans, its status and its text fields.
Span = Tuple[int, Optional[str], Dict[str, str]]
# One call and the position of the span that settled it, None when no span did.
Settled = Tuple[ToolCall, Optional[int]]
# The longest message a turn_error keeps after its class name, and how a cut message ends.
TURN_ERROR_MESSAGE_LIMIT = 300
CUT_MARK = "..."
# The longest class name a turn_error keeps, as for a class the Runtime names
# (``backend.agentcore.runtime.ERROR_TYPE``).
CLASS_NAME_LIMIT = 64
# The text up to the last word that ends before a space.
LAST_WHOLE_WORD = re.compile(r"(.*\S)\s", re.DOTALL)


@dataclass(frozen=True)
class TurnAudit:
    """The facts an audit row records about one turn.

    Attributes:
        tool_calls: Every tool call, in the order the calls started; None when the spans cannot
            say which result belongs to which call.
        policy_denied: The Runtime reported a Cedar denial for the turn's governed write.
        turn_error: The error that ended the turn before its row was written
            (``turn_error_of``). None only for a turn that completed, or one that failed after
            its row was committed (the memory step and the trace entries that follow it).
    """

    tool_calls: Optional[List[ToolCall]]
    policy_denied: bool = False
    turn_error: Optional[str] = None

    @property
    def decision(self) -> str:
        """The value for ``authorization_decision``.

        It follows from the calls and the turn's error
        (``backend.audit_vocabulary.authorization_decision``). With the calls unknown it is
        ``deny`` when the Runtime reported a Cedar denial and otherwise ``error``, meaning no
        authorization layer's decision is known.
        """
        if self.tool_calls is None:
            return "deny" if self.policy_denied else "error"
        return authorization_decision(self.tool_calls, turn_error=self.turn_error)


class TurnRead(NamedTuple):
    """What one pass over a Runtime decision yields.

    Attributes:
        audit: The facts the audit row records.
        spans: The Runtime's spans in arrival order, each refusal marked as the row records it.
    """

    audit: TurnAudit
    spans: List[Dict[str, Any]]


def _telemetry(span: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The span's telemetry, empty when it has none, or None when it cannot be read.

    Telemetry that is not a dict, or whose ``fields`` is not a list of dicts, cannot be read;
    the router's ``TraceTelemetry`` rejects the same shapes.
    """
    telemetry = span.get("telemetry")
    if telemetry is None:
        return {}
    if not isinstance(telemetry, dict):
        return None
    fields = telemetry.get("fields")
    if fields is None:
        return telemetry
    readable = isinstance(fields, list) and all(isinstance(field, dict) for field in fields)
    return telemetry if readable else None


def _fields(span: Dict[str, Any]) -> Dict[str, str]:
    """The span's text fields by label; a field without a text value says nothing, and a span
    that cannot be read has none."""
    return {
        str(field.get("label")): field["value"]
        for field in (_telemetry(span) or {}).get("fields") or []
        if isinstance(field.get("value"), str)
    }


def _grant_refusal(fields: Dict[str, str]) -> Verdict:
    """The Lambda's grant answer names the layer only beside the deny row it kept."""
    audit_id = fields.get("access_audit_id")
    if fields.get("traveler_grant") == "deny" and audit_id:
        return REFUSED, WORKLOAD_GRANT, WORKLOAD_GRANT_REFUSAL, audit_id
    return ERROR, None, WORKLOAD_GRANT_REFUSAL, None


def _failure(fields: Dict[str, str]) -> Verdict:
    text = fields.get("gateway_error", "").strip()
    if IDENTITY_REFUSAL.match(text):
        return REFUSED, IDENTITY_CHECK, identity_refusal_code(text), None
    if text == WORKLOAD_GRANT_REFUSAL:
        return _grant_refusal(fields)
    code_class = CODE_CLASSES.get(text)
    if code_class in (DATABASE_GUARD, BUSINESS_RULE):
        return REFUSED, code_class, text, None
    return ERROR, None, text if code_class else None, None


def _verdict(status: Optional[str], fields: Dict[str, str]) -> Optional[Verdict]:
    """How one span says its call ended, or None for the span before the call ran."""
    if status == "denied" or fields.get("cedar_decision") == "deny":
        return REFUSED, POLICY, None, None
    if fields.get("refused_by") == RUNTIME_GUARD:
        return REFUSED, RUNTIME_GUARD, fields.get("refusal_code") or None, None
    if status == "error":
        return _failure(fields)
    if "result" in fields:
        return ALLOWED, None, None, None
    return None


def _call(spans: List[Span]) -> Optional[Settled]:
    """One call from its spans, with the span that settled it; None when no span names the tool."""
    tool = next((fields["tool"] for _, _, fields in spans if fields.get("tool")), None)
    if tool is None:
        return None
    # A call whose result span never arrived ended in a way the spans do not show.
    verdict: Verdict = (ERROR, None, None, None)
    settled_by: Optional[int] = None
    for position, status, fields in spans:
        found = _verdict(status, fields)
        if found is not None:
            verdict, settled_by = found, position
    return ToolCall(bare_tool_name(tool), *verdict), settled_by


def _stores_as_json(fields: Dict[str, str]) -> bool:
    """Whether the labels a row stores hold text that PostgreSQL ``jsonb`` can store."""
    return all(_storable(fields.get(label, "")) == fields.get(label, "") for label in STORED_LABELS)


def _settled_calls(spans: List[Dict[str, Any]]) -> Optional[List[Settled]]:
    """Group the call spans by ``call_id`` in arrival order; None when that is not possible.

    A span that cannot be read may have been a call's, so a list read without it could leave a
    call out. The calls are then unknown. So are they when a call span holds a tool, refusal
    code or access audit id that PostgreSQL ``jsonb`` cannot store (a NUL or a lone surrogate).
    """
    by_call: Dict[str, List[Span]] = {}
    for position, span in enumerate(spans):
        telemetry = _telemetry(span)
        if telemetry is None:
            return None
        fields = _fields(span)
        if not any(label in fields for label in CALL_LABELS):
            continue
        if not _stores_as_json(fields):
            return None
        call_id = fields.get("call_id")
        if not call_id:
            return None
        by_call.setdefault(call_id, []).append((position, telemetry.get("status"), fields))
    calls = [_call(call_spans) for call_spans in by_call.values()]
    if any(call is None for call in calls):
        return None
    return calls


def _spans(decision: RuntimeDecision) -> List[Dict[str, Any]]:
    return [span for span in decision.activities if isinstance(span, dict)]


def read_turn(decision: RuntimeDecision) -> TurnRead:
    """Read every tool call of the turn, and how each ended, from a Runtime decision.

    One pass settles the calls and the spans that settled them, so the trace is marked from the
    same entries the row stores and nothing can disagree between them.

    Args:
        decision: What the managed Runtime reported for the turn.

    Returns:
        The audit facts and the spans as the trace shows them. When the Runtime reported a Cedar
        denial that no call accounts for, the calls are recorded as unknown rather than as a
        list that leaves the denial out, and every span stays as the Runtime sent it.
    """
    spans = _spans(decision)
    settled = _settled_calls(spans)
    policy_denied = decision.policy_decision == "deny"
    if settled is None or (policy_denied and not any(call.refused_by == POLICY
                                                      for call, _ in settled)):
        return TurnRead(TurnAudit(None, policy_denied), spans)
    traced = list(spans)
    for call, position in settled:
        if position is not None and call.result == REFUSED and call.refused_by != POLICY:
            traced[position] = _refused(spans[position], call)
    return TurnRead(TurnAudit([call for call, _ in settled], policy_denied), traced)


def audit_from_decision(decision: RuntimeDecision) -> TurnAudit:
    """The audit facts of a Runtime decision, as ``read_turn`` reads them."""
    return read_turn(decision).audit


def audit_of_failed_turn(reported: Optional[RuntimeDecision], turn_error: str) -> TurnAudit:
    """The audit facts of a turn whose Runtime call raised instead of returning a decision.

    Args:
        reported: What the Runtime reported before the error event that ended the turn, or None
            when the call raised without one: a transport error, a timeout, a broken stream, a
            cancellation, or a failure before the request was sent.
        turn_error: The error the call raised, as ``turn_error_of`` words it.

    Returns:
        The spans' reading by ``read_turn``, in which the failing call's own span records it as
        an error, with ``turn_error`` set, so the decision is ``deny`` or ``error``, never
        ``allow``. With nothing reported, or no span before the error, the calls are unknown and
        the decision is ``error``.
    """
    if reported is None or not _spans(reported):
        return TurnAudit(None, turn_error=turn_error)
    return replace(read_turn(reported).audit, turn_error=turn_error)


def _cut(message: str) -> str:
    """The message, or its whole words that fit in 300 characters with "..." after them.

    A first word longer than that has no boundary to cut at, so it is cut where the room ends.
    """
    if len(message) <= TURN_ERROR_MESSAGE_LIMIT:
        return message
    room = TURN_ERROR_MESSAGE_LIMIT - len(CUT_MARK)
    whole = LAST_WHOLE_WORD.match(message, 0, room + 1)
    return (whole.group(1) if whole else message[:room]) + CUT_MARK


def _storable(text: str) -> str:
    """The text as PostgreSQL text can store it: valid UTF-8, without NUL characters.

    UTF-8 cannot encode a lone surrogate, which JSON can carry as an escape; each one becomes
    one U+FFFD, the character Unicode designates for text that could not be represented.
    """
    return LONE_SURROGATE.sub("\ufffd", text).replace("\x00", "")


def turn_error_class(error: BaseException) -> str:
    """The class a ``turn_error`` names for ``error``, at most its first 64 characters.

    A Runtime error event gives the class the Runtime named; when the event names no class (a
    Runtime deployed before it sent ``error_type``), the class is ``RuntimeTurnError``. Any
    other error gives its own class. Reading it runs none of the error's own code.
    """
    if isinstance(error, RuntimeTurnError):
        return (error.error_type or RuntimeTurnError.__name__)[:CLASS_NAME_LIMIT]
    return type(error).__name__[:CLASS_NAME_LIMIT]


def turn_error_of(error: BaseException) -> str:
    """The ``turn_error`` of a turn that ``error`` ended before its row was written.

    The class is ``turn_error_class(error)``. A Runtime error event gives the event's message;
    any other error, such as a transport failure, a timeout, an expired token, a cancellation
    or a failure after the Runtime returned, gives its own text.

    Args:
        error: The exception that ended the turn.

    Returns:
        The class, then ": " and the message as PostgreSQL text can store it (valid UTF-8, each
        lone surrogate replaced by U+FFFD, without NUL characters), with account ids masked, cut
        at a word boundary to at most 300 characters ending with "...". The class alone when
        the message is empty. Every result passes
        ``backend.audit_vocabulary.turn_error_problem``.

    Raises:
        Exception: Whatever the error's own ``__str__`` raises. A writer then names the error
            by ``turn_error_class`` alone.
    """
    message = error.event_message if isinstance(error, RuntimeTurnError) else str(error)
    message = _cut(mask_account_ids(_storable(message).strip()))
    name = turn_error_class(error)
    return f"{name}: {message}" if message else name


def layer_words(layer: str) -> str:
    """A refusing layer as the trace writes it, for example ``business rule``."""
    return layer.replace("_", " ")


def _refused_title(call: ToolCall) -> str:
    """The refused span's title, such as ``Hold refused: business rule``."""
    subject = WRITE_NOUNS.get(call.tool, call.tool)
    return f"{subject} refused: {layer_words(str(call.refused_by))}"


def _refused(span: Dict[str, Any], call: ToolCall) -> Dict[str, Any]:
    """A copy of the span that settled a refused call, titled and marked from its entry."""
    telemetry = dict(span.get("telemetry") or {})
    fields = [field for field in telemetry.get("fields") or []
              if not (isinstance(field, dict) and field.get("label") == "refused_by")]
    telemetry["status"] = REFUSED_STATUS
    telemetry["fields"] = [*fields, {"label": "refused_by", "value": call.refused_by}]
    return {**span, "title": _refused_title(call), "telemetry": telemetry}


def write_refusal(audit: Optional[TurnAudit], tool: str) -> Optional[str]:
    """The layer other than Cedar that refused the turn's governed write, or None.

    Args:
        audit: The turn's audit facts, or None when they were not read.
        tool: The governed write's contract name. Its first call is the write; any later call
            is a retry the Runtime declined.

    Returns:
        The refusing layer from the row's entry for that call. None when the write was allowed,
        denied by Cedar, ended in an error, was never called, or the calls are unknown.
    """
    calls = (audit.tool_calls if audit else None) or []
    write = next((call for call in calls if call.tool == tool), None)
    if write is None or write.result != REFUSED or write.refused_by == POLICY:
        return None
    return write.refused_by
