"""The words an audit row uses for each tool call, shared by Phase 4, Phase 5 and the store.

An audit row lists every tool call of a turn, in the order the calls started. Each call ended
``allowed``, ``refused`` or ``error``. A refusal names the layer whose own evidence refused it,
in the order a call meets them:

- ``identity_check``: the Gateway request interceptor found no usable traveler in the token.
- ``policy``: Cedar denied the call.
- ``workload_grant``: the holds Lambda's own traveler grant check denied it, proven by the
  ``traveler_access_audit`` deny row it wrote.
- ``database_guard``: an Aurora function or the Lambda rejected arguments that do not match the
  authorized scope or the stored record.
- ``business_rule``: the caller was authorized and the arguments matched, but a business rule
  declined (stock, budget, hold expiry, quantity, booking state).
- ``runtime_guard``: the Runtime declined a second governed write after the first was attempted
  on the same turn. It is not a security layer.

``error`` covers everything no layer claims: run ownership conflicts, unknown text, transport
failures. The migration 021 CHECKs hold the same words; a test keeps the two in step.

A turn that an error ended before its row was written says so in ``turn_error``: the error's
class and a short message. Such a row is never ``allow``. ``turn_error`` stays NULL only for a
turn that completed, or one that failed after its row was committed (the memory step and the
trace entries that follow it).
"""

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional

from backend.security.redaction import mask_secrets

ALLOWED = "allowed"
REFUSED = "refused"
ERROR = "error"
CALL_RESULTS = (ALLOWED, REFUSED, ERROR)

IDENTITY_CHECK = "identity_check"
POLICY = "policy"
WORKLOAD_GRANT = "workload_grant"
DATABASE_GUARD = "database_guard"
BUSINESS_RULE = "business_rule"
RUNTIME_GUARD = "runtime_guard"
REFUSING_LAYERS = (
    IDENTITY_CHECK, POLICY, WORKLOAD_GRANT, DATABASE_GUARD, BUSINESS_RULE, RUNTIME_GUARD,
)
# A refusal by one of these is an authorization decision against the caller's request.
DENYING_LAYERS = frozenset({IDENTITY_CHECK, POLICY, WORKLOAD_GRANT, DATABASE_GUARD})
# The holds Lambda's answer when its own traveler grant check refuses the call.
WORKLOAD_GRANT_REFUSAL = "traveler_not_authorized"
# The longest turn_error a row stores; migration 021 checks the same limit.
TURN_ERROR_LIMIT = 400
# The longest message a turn_error keeps after its class name, and how a cut message ends.
TURN_ERROR_MESSAGE_LIMIT = 300
CUT_MARK = "..."
# The longest class name a turn_error keeps, as for a class the Runtime names
# (``backend.agentcore.runtime.ERROR_TYPE``).
CLASS_NAME_LIMIT = 64
# The text up to the last word that ends before a space.
LAST_WHOLE_WORD = re.compile(r"(.*\S)\s", re.DOTALL)
LONE_SURROGATE = re.compile("[\ud800-\udfff]")
# The Runtime's code for a second governed write it declined to send on the same turn. It says
# attempted, not decided: a first attempt that failed in transport may have reached no decision.
WRITE_ALREADY_ATTEMPTED = "write_already_attempted"

# How each named code the holds Lambda returns is classified, read from the code that raises it:
# create_courtesy_hold (scripts/migrations/008), confirm_booking (010) and the Lambda itself.
# invalid_package_inventory is an error because one of its raising branches is a catalog data
# fault, not the caller's argument. tests/test_holds_lambda.py keeps the keys equal to the
# Lambda's BUSINESS_ERRORS.
CODE_CLASSES = {
    "execution_lease_lost": ERROR,
    "insufficient_inventory": BUSINESS_RULE,
    "invalid_package_inventory": ERROR,
    "journey_not_owned": DATABASE_GUARD,
    "traveler_scope_mismatch": DATABASE_GUARD,
    "booking_agent_not_authorized": DATABASE_GUARD,
    "invalid_hold_quantity": BUSINESS_RULE,
    "hold_request_parameter_mismatch": DATABASE_GUARD,
    "booking_not_found": DATABASE_GUARD,
    "hold_not_found": DATABASE_GUARD,
    "booking_amount_mismatch": DATABASE_GUARD,
    "booking_not_held": BUSINESS_RULE,
    "hold_expired": BUSINESS_RULE,
    "hold_price_mismatch": DATABASE_GUARD,
    "hold_total_mismatch": DATABASE_GUARD,
    "budget_ceiling_exceeded": BUSINESS_RULE,
}

# The interceptor answers with this prefix. Phase 5 reads the Gateway's text as sent; the Phase 4
# Runtime may see it behind the Strands wrapper for a tool that raised.
IDENTITY_REFUSAL = re.compile(r"^(?:Tool execution failed:\s*)?Identity Check Failed:\s*")
# The interceptor's reason sentences, mapped back to its reason keys. "internal" is its fail-closed
# answer: the check crashed, which is not an identity mismatch. tests/test_audit_vocabulary.py
# keeps this equal to the interceptor's REFUSALS.
IDENTITY_REFUSAL_REASONS = {
    "the request could not be read.": "malformed",
    "the Gateway did not pass the caller's headers to the interceptor.": "no_headers",
    "the request carries no bearer token.": "no_token",
    "the bearer token is not an access token this system issues.": "token",
    "the request carries more than one Authorization header.": "ambiguous",
    "the access token has expired or carries no expiry.": "expired",
    "the access token carries no single traveler for this system.": "claim",
    "the identity check could not be completed.": "internal",
}


@dataclass(frozen=True)
class ToolCall:
    """One tool call as an audit row records it.

    Attributes:
        tool: The contract name, without the Gateway target prefix.
        result: ``allowed``, ``refused`` or ``error``.
        refused_by: The refusing layer, only when ``result`` is ``refused``.
        refusal_code: The named code the evidence carries, else None.
        access_audit_id: The ``traveler_access_audit`` row of a ``workload_grant`` refusal.
    """

    tool: str
    result: str
    refused_by: Optional[str] = None
    refusal_code: Optional[str] = None
    access_audit_id: Optional[str] = None

    def as_json(self) -> Dict[str, Any]:
        """The element stored in ``agent_audit_log.tool_calls``."""
        return {
            "tool": self.tool,
            "result": self.result,
            "refused_by": self.refused_by,
            "refusal_code": self.refusal_code,
            "access_audit_id": self.access_audit_id,
        }


def _optional_text(value: Any) -> bool:
    return value is None or (isinstance(value, str) and bool(value))


def tool_call_problem(call: Any) -> Optional[str]:
    """Why ``call`` cannot be stored, or None when it fits the vocabulary."""
    if not isinstance(call, ToolCall):
        return f"expected a ToolCall, got {type(call).__name__}"
    if not isinstance(call.tool, str) or not call.tool:
        return "tool must be a non-empty name"
    if call.result not in CALL_RESULTS:
        return f"result must be one of {CALL_RESULTS}, got {call.result!r}"
    if call.result == REFUSED and call.refused_by not in REFUSING_LAYERS:
        return f"a refusal needs refused_by from {REFUSING_LAYERS}, got {call.refused_by!r}"
    if call.result != REFUSED and call.refused_by is not None:
        return f"refused_by is only valid for a refusal, got {call.refused_by!r}"
    if not _optional_text(call.refusal_code) or not _optional_text(call.access_audit_id):
        return "refusal_code and access_audit_id must be None or a non-empty text"
    if (call.refused_by == WORKLOAD_GRANT) != (call.access_audit_id is not None):
        return "access_audit_id is required for, and only for, a workload_grant refusal"
    return None


def turn_error_problem(turn_error: Any, decision: Optional[str]) -> Optional[str]:
    """Why a row cannot store ``turn_error`` with ``decision``, or None when it can.

    None stands for NULL. ``turn_error`` stays NULL only for a turn that completed, or one that
    failed after its row was committed (the memory step and the trace entries that follow it).
    Otherwise it is a non-empty text of at most 400 characters, and the row's
    ``authorization_decision`` is not ``allow``.
    """
    if turn_error is None:
        return None
    if not isinstance(turn_error, str) or not turn_error or len(turn_error) > TURN_ERROR_LIMIT:
        return (f"turn_error must be None or a non-empty text of at most {TURN_ERROR_LIMIT} "
                "characters")
    if decision == "allow":
        return "a row with a turn_error cannot have authorization_decision 'allow'"
    return None


def storable_text(text: str) -> str:
    """The text as PostgreSQL text can store it: valid UTF-8, without NUL characters.

    UTF-8 cannot encode a lone surrogate, which JSON can carry as an escape; each one becomes
    one U+FFFD, the character Unicode designates for text that could not be represented.
    """
    return LONE_SURROGATE.sub("\ufffd", text).replace("\x00", "")


def _cut(message: str) -> str:
    """The message, or its whole words that fit in 300 characters with "..." after them.

    A first word longer than that has no boundary to cut at, so it is cut where the room ends.
    """
    if len(message) <= TURN_ERROR_MESSAGE_LIMIT:
        return message
    room = TURN_ERROR_MESSAGE_LIMIT - len(CUT_MARK)
    whole = LAST_WHOLE_WORD.match(message, 0, room + 1)
    return (whole.group(1) if whole else message[:room]) + CUT_MARK


def format_turn_error(class_name: str, message: str) -> str:
    """``class_name``, then ": " and the message as a row stores it; the class alone when empty.

    The message is made storable, masked of account ids, then cut at a word boundary to at most
    300 characters ending with "...". Every result passes :func:`turn_error_problem`.
    """
    message = _cut(mask_secrets(storable_text(message).strip()))
    return f"{class_name}: {message}" if message else class_name


def turn_error_of(error: BaseException) -> str:
    """The ``turn_error`` of a run that ``error`` ended: its class (at most 64 characters) and text.

    Raises:
        Exception: Whatever the error's own ``__str__`` raises. A writer then names the error by
            its class alone.
    """
    return format_turn_error(type(error).__name__[:CLASS_NAME_LIMIT], str(error))


def identity_refusal_code(text: str) -> Optional[str]:
    """The interceptor's reason key for its refusal text, or None for any other text."""
    found = IDENTITY_REFUSAL.match(text.strip())
    if not found:
        return None
    return IDENTITY_REFUSAL_REASONS.get(text.strip()[found.end():].strip())


def authorization_decision(
    calls: Iterable[ToolCall], *, turn_error: Optional[str] = None
) -> str:
    """The row's ``authorization_decision`` for these calls and the turn's error.

    ``deny`` when any call was refused by ``identity_check``, ``policy``, ``workload_grant`` or
    ``database_guard``; otherwise ``error`` when any call ended in ``error`` or ``turn_error`` is
    set, because an error ended the turn before its row was written; otherwise ``allow``, which
    includes ``business_rule`` and ``runtime_guard`` refusals and a turn with no calls.
    ``turn_error`` stays NULL only for a turn that completed, or one that failed after its row
    was committed (the memory step and the trace entries that follow it).

    ``error`` says that a call failed for a reason no authorization layer claimed, or that the
    turn failed outside its calls, for example when IAM refused the model call or when the trace
    or catalog refresh failed after the Runtime returned. A failed call has no layer's decision
    known; for a turn that failed outside its calls, the listed calls still show each layer's
    decision. A turn whose model recovered from a failed call
    is still ``error``, and a denial outranks a turn that then failed.
    """
    calls = list(calls)
    if any(call.refused_by in DENYING_LAYERS for call in calls):
        return "deny"
    if turn_error is not None or any(call.result == ERROR for call in calls):
        return "error"
    return "allow"
