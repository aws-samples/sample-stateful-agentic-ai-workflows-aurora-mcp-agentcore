"""Place the workflow's courtesy hold through the governed gateway tool.

Phase 5 asks AgentCore Gateway for the same hold the Phase 4 agent uses,
so Cedar sees every hold the application places and there is one
door for business effects. The worker keeps its own lease check; it also passes
its execution id so the Lambda verifies the lease again inside the write
transaction, and it passes the checkpointed request and booking ids so a
replacement worker replays the same booking with the original expiry.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

# The Gateway's request interceptor answers a call whose caller has no usable traveler with a tool
# error behind IDENTITY_REFUSAL's prefix. Nothing ran, so it is a refusal, not an unknown outcome.
from backend.audit_vocabulary import CODE_CLASSES
from backend.audit_vocabulary import IDENTITY_CHECK as REFUSED_BY_IDENTITY
from backend.audit_vocabulary import IDENTITY_REFUSAL, WORKLOAD_GRANT_REFUSAL, identity_refusal_code
from backend.audit_vocabulary import POLICY as REFUSED_BY_POLICY
from backend.security.redaction import mask_secrets

logger = logging.getLogger(__name__)

HOLD_TOOL = "MeridianHolds___create_courtesy_hold"
RELEASE_TOOL = "MeridianHolds___release_hold"
DETAIL_LIMIT = 200
ENVELOPE_PHRASES = {
    REFUSED_BY_POLICY: "the Gateway policy denied the call",
    REFUSED_BY_IDENTITY: "the Gateway identity check refused the call",
    None: "the Gateway answered with an error",
}
UNREADABLE_RESULT = "the Gateway result could not be read"
TARGET_ERROR = "the hold target returned an error"
NO_RELEASE_RECEIPT = "the Gateway returned no release receipt"
# Gateway Policy denial envelopes: tool-result errors and the observed JSON-RPC
# -32002 response. IAM and target errors must not become Cedar decisions.
DENIAL = re.compile(r"^(?:AuthorizeActionException\s*-\s*)?Tool Execution Denied:", re.I)


# Lambda errors proving the calling worker no longer owns the run. ``journey_not_owned`` is a
# traveler-ownership guard in the database (migration 008; class ``database_guard`` in
# ``audit_vocabulary.CODE_CLASSES``) and the hold step treats it as a lost lease too, so the
# run's audit row shows both: the call is a ``database_guard`` refusal and the run's
# ``turn_error`` names ``ExecutionLeaseLostError``.
MALFORMED_RESPONSE = "unreadable gateway response"
LEASE_LOST_ERRORS = frozenset({"execution_lease_lost", "journey_not_owned"})


class HoldOutcomeUnknown(RuntimeError):
    """A hold may have committed; preserve its checkpointed identity for retry."""


@dataclass(frozen=True)
class GovernedHold:
    """The gateway's answer to one hold call.

    Attributes:
        error: A code or a fixed phrase, never text the Gateway or the target sent: the value
            reaches the saved snapshot, the trace and the traveler's screen. The sent text goes to
            a masked, bounded log record (:func:`log_detail`). A code is a name in
            ``CODE_CLASSES`` or the workload-grant refusal.
        refused_by: ``"policy"`` when Cedar denied the call, ``"identity_check"`` when the
            request interceptor refused it before Cedar ran, None otherwise. Either
            refusal means nothing reached the target.
    """

    hold: Optional[Dict[str, Any]]
    governance: Dict[str, Any]
    error: Optional[str]
    policy_decision: Optional[str]
    refused_by: Optional[str] = None

    @property
    def placed(self) -> bool:
        return self.hold is not None


def hold_arguments(
    intent: Dict[str, Any], *, traveler_id: str, journey_ref: str,
    budget_ceiling_cents: int, hold_minutes: int, execution_id: Optional[str],
    traveler_confirmed: bool,
) -> Dict[str, Any]:
    """Tool arguments from the checkpointed hold intent.

    ``traveler_confirmed`` must come from the traveler's own resume after
    reviewing the plan, never from a model and never from reaching this step.
    The Cedar policy denies the hold when it is false.
    """
    unit_price_cents = int(round(float(intent["unit_price"]) * 100))
    quantity = int(intent["quantity"])
    args = {
        "travelerId": traveler_id,
        "packageId": str(intent["package_id"]),
        "duration": str(intent["duration"]),
        "travelers": quantity,
        "unitPriceCents": unit_price_cents,
        "totalCents": unit_price_cents * quantity,
        "holdMinutes": int(hold_minutes),
        "travelerConfirmed": bool(traveler_confirmed),
        "budgetCeilingCents": int(budget_ceiling_cents),
        "journeyRef": journey_ref,
        "holdRequestId": str(intent["hold_request_id"]),
        "bookingId": str(intent["booking_id"]),
    }
    if execution_id:
        args["executionId"] = str(execution_id)
    return args


def scrub_detail(detail: str) -> str:
    """Gateway or exception text made safe to log or print, cut to ``DETAIL_LIMIT`` characters.

    ``mask_secrets`` masks bearer tokens, credentials and account ids, as stored text does.
    """
    return mask_secrets(detail)[:DETAIL_LIMIT]


def log_detail(tool: str, detail: str) -> None:
    """Log what a Gateway failure said for operators, scrubbed by :func:`scrub_detail`.

    This is the only place failure text from the Gateway or a target is kept.
    """
    scrubbed = scrub_detail(detail)
    logger.warning("Gateway tool %s failure detail: %s", tool, scrubbed)


def envelope_error(layer: Optional[str], message: str) -> str:
    """The fixed phrase for a failed envelope, plus the interceptor reason key when given."""
    phrase = ENVELOPE_PHRASES[layer]
    code = identity_refusal_code(message) if layer == REFUSED_BY_IDENTITY else None
    return f"{phrase} ({code})" if code else phrase


def response_text(response: Any) -> str:
    """The joined text blocks of a ``tools/call`` response; empty for any other shape."""
    result = response.get("result") if isinstance(response, dict) else None
    content = result.get("content") if isinstance(result, dict) else None
    if not isinstance(content, list):
        return ""
    return "".join(
        block["text"]
        for block in content
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    )


def refusal_layer_of(message: str) -> Optional[str]:
    if DENIAL.match(message):
        return REFUSED_BY_POLICY
    if IDENTITY_REFUSAL.match(message):
        return REFUSED_BY_IDENTITY
    return None


def envelope_failure(response: Any, text: str) -> Optional[Tuple[str, Optional[str]]]:
    """The message and refusing layer of an MCP error envelope, or None when the call answered.

    Args:
        response: A ``tools/call`` JSON-RPC response.
        text: Its joined text content, from :func:`response_text`.

    Returns:
        ``(message, layer)`` where layer is ``"policy"``, ``"identity_check"`` or None.
    """
    if not isinstance(response, dict) or not isinstance(response.get("result") or {}, dict):
        return MALFORMED_RESPONSE, None
    rpc_error = response.get("error")
    result = response.get("result") or {}
    if not (rpc_error or result.get("isError")):
        return None
    rpc_message = rpc_error.get("message") if isinstance(rpc_error, dict) else None
    message = (
        text
        or (rpc_message if isinstance(rpc_message, str) else "")
        or json.dumps(rpc_error or result)[:300]
    )
    policy_envelope = bool(result.get("isError")) or (
        isinstance(rpc_error, dict) and rpc_error.get("code") == -32002
    )
    return message, (refusal_layer_of(message.strip()) if policy_envelope else None)


def target_error(code: Any) -> str:
    """A target's error word when it is a known code, else a fixed phrase."""
    known = isinstance(code, str) and (code in CODE_CLASSES or code == WORKLOAD_GRANT_REFUSAL)
    return code if known else TARGET_ERROR


def place_governed_hold(call_tool: Callable[[str, Dict[str, Any]], Dict[str, Any]],
                        arguments: Dict[str, Any]) -> GovernedHold:
    """Call the gateway hold tool and normalise what came back.

    Args:
        call_tool: ``(tool_name, arguments) -> MCP tools/call response`` (JSON-RPC dict).
        arguments: From :func:`hold_arguments`.

    Returns:
        A GovernedHold. ``error`` is the Lambda's named business error, the Cedar
        denial text, or the transport error; ``policy_decision`` is ``"deny"`` when
        Cedar refused the call and ``"allow"`` when the Lambda ran. An identity
        refusal carries no policy decision, because Cedar never saw the call.
    """
    response = call_tool(HOLD_TOOL, arguments)
    text = response_text(response)
    failure = envelope_failure(response, text)
    if failure is not None:
        message, refused_by = failure
        log_detail(HOLD_TOOL, message)
        decision = "deny" if refused_by == REFUSED_BY_POLICY else None
        error = envelope_error(refused_by, message)
        return GovernedHold(None, {}, error, decision, refused_by)
    try:
        payload = json.loads(text) if text else {}
    except ValueError:
        log_detail(HOLD_TOOL, text)
        return GovernedHold(None, {}, UNREADABLE_RESULT, None)
    if not isinstance(payload, dict):
        return GovernedHold(None, {}, UNREADABLE_RESULT, None)
    code = payload.get("error")
    if code is not None:
        log_detail(HOLD_TOOL, str(code))
        return GovernedHold(None, payload.get("governance") or {}, target_error(code), "allow")
    return GovernedHold(payload.get("hold"), payload.get("governance") or {}, None, "allow")


@dataclass(frozen=True)
class GovernedRelease:
    """The gateway's answer to one release call.

    Attributes:
        released: True when the booking is released, newly or by an earlier call.
        error: A code or a fixed phrase, never text the Gateway or the target sent (see
            :class:`GovernedHold`); None on success.
        refused_by: ``"policy"`` or ``"identity_check"`` when a layer before the Lambda refused.
        replayed: True when the booking was already released.
        lease_lost: True when the target said this worker no longer owns the run; the booking
            was not released.
    """

    released: bool
    error: Optional[str]
    refused_by: Optional[str] = None
    replayed: bool = False
    lease_lost: bool = False


def release_arguments(
    *, traveler_id: str, hold_request_id: str, journey_id: str, execution_id: Optional[str] = None
) -> Dict[str, Any]:
    """Tool arguments for giving back one hold, from the saved request identity.

    Args:
        traveler_id: The verified caller's traveler id, from the caller claims only. Never a
            value a model or a request body supplied.
        hold_request_id: The request id the hold was placed under.
        journey_id: The journey id from the hold receipt.
        execution_id: The calling worker's execution id; the target then refuses the release
            unless that worker's lease is still valid.
    """
    args = {
        "travelerId": traveler_id,
        "holdRequestId": hold_request_id,
        "journeyId": journey_id,
    }
    if execution_id:
        args["executionId"] = str(execution_id)
    return args


def release_governed_hold(
    call_tool: Callable[[str, Dict[str, Any]], Dict[str, Any]], arguments: Dict[str, Any]
) -> GovernedRelease:
    """Call the gateway release tool and normalise what came back.

    Args:
        call_tool: ``(tool_name, arguments) -> MCP tools/call response``.
        arguments: From :func:`release_arguments`.

    Returns:
        A GovernedRelease; ``released`` is True only for a receipt whose status is released.
    """
    response = call_tool(RELEASE_TOOL, arguments)
    text = response_text(response)
    failure = envelope_failure(response, text)
    if failure is not None:
        message, refused_by = failure
        log_detail(RELEASE_TOOL, message)
        return GovernedRelease(False, envelope_error(refused_by, message), refused_by)
    try:
        payload = json.loads(text) if text else {}
    except ValueError:
        log_detail(RELEASE_TOOL, text)
        return GovernedRelease(False, UNREADABLE_RESULT)
    if not isinstance(payload, dict):
        return GovernedRelease(False, UNREADABLE_RESULT)
    code = payload.get("error")
    if code is not None:
        log_detail(RELEASE_TOOL, str(code))
        return GovernedRelease(False, target_error(code), lease_lost=code in LEASE_LOST_ERRORS)
    release = payload.get("release")
    if not isinstance(release, dict) or release.get("status") != "released":
        return GovernedRelease(False, NO_RELEASE_RECEIPT)
    return GovernedRelease(True, None, replayed=bool(release.get("replayed")))
