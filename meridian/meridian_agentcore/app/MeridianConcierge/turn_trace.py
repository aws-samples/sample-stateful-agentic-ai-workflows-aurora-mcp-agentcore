"""Trace hooks: every gateway tool call becomes a span the showcase can render.

The hooks also pin the governed-write contract to the turn. The model proposes a
hold or a booking confirmation; the traveler id, the confirmation flag, the
budget ceiling and the journey reference always come from the request the
backend authorized, never from the model. The gateway's Cedar policies then
decide with those values.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider

from concierge_definition import (
    CATALOG_TOOLS,
    DETAIL_TOOLS,
    PACKAGE_SEARCH_TOOLS,
    TRAVELER_TOOLS,
    pin_traveler_argument,
    short_name,
)
from tool_errors import mask_account_ids

AGENT_FILE = "meridian_agentcore/app/MeridianConcierge/main.py"
POLICY_REASONS = {
    "meridian_hold_governance": (
        "A courtesy hold runs only after the traveler confirms it, for 12 hours or less, "
        "for at most 6 travelers, and within the traveler's saved budget ceiling."
    ),
    "meridian_booking_governance": (
        "A held booking is confirmed only after the traveler confirms it and only when its "
        "total is within the traveler's saved budget ceiling."
    ),
}
HOLD_LIMITS = {"holdMinutes": 720, "travelers": 6}
# The policy that must permit each tool. The engine is default deny with one permit
# per action, so a call that ran was permitted by this policy and a denied call
# found no permit in it. The gateway reports the decision, not the policy name.
POLICY_FOR = {
    **{name: "meridian_read_tools" for name in CATALOG_TOOLS + DETAIL_TOOLS + ("loyalty_balance",)},
    "create_courtesy_hold": "meridian_hold_governance",
    "confirm_booking": "meridian_booking_governance",
}
# The governed writes, named by the outcome each one settles. A turn attempts
# each of them at most once.
GOVERNED = {"create_courtesy_hold": "hold", "confirm_booking": "booking"}
# How the audit row names a governed write the Runtime declined to send twice on one turn;
# backend/audit_vocabulary.py holds the same words.
RUNTIME_GUARD = "runtime_guard"
WRITE_ALREADY_ATTEMPTED = "write_already_attempted"
SPANS = {
    "search_trips": (
        "search",
        "AgentCore Gateway: tools/call → search_trips",
        "Catalog filter with every value bound as a parameter, through the managed MCP tool",
    ),
    "compare_packages": (
        "tool_call",
        "AgentCore Gateway: tools/call → compare_packages",
        "Side-by-side rows for the named packages",
    ),
    "price_range": (
        "tool_call",
        "AgentCore Gateway: tools/call → price_range",
        "Low, average and high price for a destination",
    ),
    "region_inventory": (
        "inventory",
        "AgentCore Gateway: tools/call → region_inventory",
        "Package count and departure places for a region",
    ),
    "currency_convert": (
        "tool_call",
        "AgentCore Gateway: tools/call → currency_convert",
        "Indicative currency conversion, not for settlement",
    ),
    "hybrid_trip_search": (
        "search",
        "AgentCore Gateway: tools/call → hybrid_trip_search",
        "Cohere Embed v4, Aurora pgvector and full-text candidates, Cohere Rerank 3.5",
    ),
    "get_package_details": (
        "inventory",
        "AgentCore Gateway: tools/call → get_package_details",
        "Live durations, availability and highlights for one package",
    ),
    "loyalty_balance": (
        "tool_call",
        "AgentCore Gateway: tools/call → loyalty_balance",
        "The traveler's own saved balance, read under the workload grant and row-level security",
    ),
    "create_courtesy_hold": (
        "order",
        "AgentCore Gateway: tools/call → create_courtesy_hold",
        "Cedar decides on the arguments, then one atomic Aurora write",
    ),
    "confirm_booking": (
        "order",
        "AgentCore Gateway: tools/call → confirm_booking",
        "Cedar decides on the arguments, then the held booking becomes confirmed in Aurora",
    ),
}
# The tools' factual "no" answers, each with the tools that send it. Each is a fact about the
# request, not a failed call, so the trace shows a result and the Phase 4 audit records the call
# as allowed (owner decision 14). The same code from any other tool is a failed call.
BUSINESS_ANSWERS = {
    "loyalty_balance_unavailable": ("loyalty_balance",),
    "unsupported_currency": ("currency_convert",),
    "unknown_package": ("get_package_details",),
}


def is_business_answer(name: str, payload: dict) -> bool:
    """True when ``payload`` is a factual "no" that the tool ``name`` is known to send."""
    code = payload.get("error")
    return isinstance(code, str) and name in BUSINESS_ANSWERS.get(code, ())
# Strands reports an MCP tool exception as "Tool execution failed: <message>",
# so the gateway's denial arrives behind that prefix. Without allowing for it
# a Cedar refusal is rendered as a generic tool failure with no decision.
DENIAL_PATTERN = re.compile(
    r"^(?:Tool execution failed:\s*)?(?:AuthorizeActionException\s*-\s*)?"
    r"Tool Execution Denied:",
    re.I,
)


@dataclass(frozen=True)
class TurnContext:
    """What the backend authorized for this turn."""

    traveler_id: str
    conversation_id: str
    hold_confirmed: bool
    budget_ceiling_cents: int
    gateway_id: str
    policy_engine_id: str
    policy_mode: str = "ENFORCE"
    booking_confirmed: bool = False
    gateway_auth: str = "SigV4"

    def confirmed(self, kind: str) -> bool:
        """Whether the traveler confirmed this kind of governed write on this turn."""
        return self.booking_confirmed if kind == "booking" else self.hold_confirmed


short = short_name


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def activity(activity_type, title, details=None, telemetry=None, elapsed_ms=None):
    """One ActivityEntry in the shape the backend's chat router already returns."""
    return {
        "id": f"rt-{uuid.uuid4().hex[:10]}",
        "timestamp": now(),
        "activity_type": activity_type,
        "title": title,
        "details": details,
        "agent_name": "ProductionAgent",
        "agent_file": AGENT_FILE,
        "execution_time_ms": elapsed_ms,
        "telemetry": telemetry,
    }


def deny_reasons(args: dict, kind: str = "hold") -> list[str]:
    """Name the conditions the arguments fail, in the order the policy states them."""
    reasons = []
    if args.get("travelerConfirmed") is not True:
        reasons.append(f"the traveler has not confirmed this {kind}")
    if kind == "hold":
        if int(args.get("holdMinutes") or 0) > HOLD_LIMITS["holdMinutes"]:
            reasons.append("the hold is longer than 12 hours")
        if int(args.get("travelers") or 0) > HOLD_LIMITS["travelers"]:
            reasons.append("more than 6 travelers")
    total, ceiling = int(args.get("totalCents") or 0), int(args.get("budgetCeilingCents") or 0)
    if total > ceiling:
        reasons.append(
            f"the total ${total / 100:,.2f} exceeds the saved budget ceiling ${ceiling / 100:,.2f}"
        )
    return reasons


def friendly_denial(text: str, args: dict | None = None, kind: str = "hold") -> str:
    """Turn the gateway's policy error into a sentence; the raw text stays in the span."""
    named = re.search(r"denied due to ([A-Za-z0-9_]+?)(?:-[a-z0-9]+_?)?\]", text)
    if named:
        policy = named.group(1)
        return f"Refused by Cedar policy {policy}. {POLICY_REASONS.get(policy, '')}".strip()
    if "No policy applies" in text or "denied by default" in text:
        reasons = deny_reasons(args, kind) if args else []
        if reasons:
            return (
                f"No Cedar policy permits this {kind}, so the gateway denied it by default: "
                + "; ".join(reasons) + "."
            )
        return "No Cedar policy permits this call, so the gateway denied it by default."
    return text


def parse_result(result: dict) -> dict | None:
    """The gateway returns the Lambda's JSON as MCP text content."""
    text = "".join(
        block.get("text", "") for block in result.get("content", []) if isinstance(block, dict)
    )
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _error_text(result: dict, payload: dict | None) -> str:
    """The failure's text with account ids masked, then cut: a cut first could leave digits."""
    if payload is not None and "error" in payload:
        return mask_account_ids(str(payload["error"]))
    return mask_account_ids(" ".join(
        block.get("text", "") for block in result.get("content", []) if isinstance(block, dict)
    ))[:600]


def _call_id(tool_use: dict) -> dict:
    """The field that ties every span of one tool call together.

    Calls the model makes in parallel report their results in completion order, so the
    backend pairs each result with its call by this id, the Strands ``toolUseId``.
    """
    return {"label": "call_id", "value": str(tool_use["toolUseId"]), "mono": True}


def _grant_fields(governance) -> list[dict]:
    """The holds Lambda's own grant refusal: its decision and the deny row it kept."""
    if not isinstance(governance, dict) or governance.get("decision") != "deny":
        return []
    fields = [{"label": "traveler_grant", "value": "deny"}]
    if governance.get("access_audit_id"):
        fields.append(
            {"label": "access_audit_id", "value": str(governance["access_audit_id"]), "mono": True}
        )
    return fields


def _summary(name: str, payload: dict) -> str:
    if name in PACKAGE_SEARCH_TOOLS:
        return f"{len(payload.get('packages') or [])} packages"
    if is_business_answer(name, payload):
        return f"{name} answered {payload['error']}"
    return f"{name} returned"


class TraceHooks(HookProvider):
    """Emit spans for every tool call and pin the governed-write arguments to the turn."""

    def __init__(self, queue, turn: TurnContext) -> None:
        self.queue = queue
        self.turn = turn
        self.started: dict[str, float] = {}
        self.hold: dict | None = None
        self.hold_settled = False
        self.booking: dict | None = None
        self.booking_settled = False
        self.packages: list[dict] = []

    def register_hooks(self, registry, **kwargs) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before)
        registry.add_callback(AfterToolCallEvent, self.after)

    def emit(self, kind: str, payload) -> None:
        self.queue.put_nowait((kind, payload))

    def _is_settled(self, kind: str) -> bool:
        return bool(getattr(self, f"{kind}_settled"))

    def _settle(self, kind: str, outcome: dict | None) -> None:
        setattr(self, kind, outcome)
        setattr(self, f"{kind}_settled", True)

    def before(self, event) -> None:
        name = short(event.tool_use["name"])
        if name not in SPANS:
            return
        args = event.tool_use.setdefault("input", {})
        if name in TRAVELER_TOOLS:
            # TravelerPinHook pins it too; pinned here as well so this span always shows
            # the traveler the call carries, whatever order a caller gives the hooks.
            pin_traveler_argument(args, self.turn.traveler_id)
        kind = GOVERNED.get(name)
        if kind:
            if self._is_settled(kind):
                # The first attempt may have been decided, or may have failed before the
                # gateway answered. Either way a second call could place the write twice or
                # retry a Cedar decision with the same pinned arguments.
                event.cancel_tool = (
                    f"This {kind} was already attempted on this turn. Do not call "
                    f"{name} again; explain the outcome to the traveler."
                )
                self._guarded(kind, event.tool_use)
                return
            self._pin_arguments(kind, args)
        span_kind, title, summary = SPANS[name]
        self.emit("activity", activity(span_kind, title, summary, {
            "category": "gateway",
            "component": "Bedrock AgentCore Gateway",
            "status": "ok",
            "fields": [
                {"label": "tool", "value": event.tool_use["name"], "mono": True},
                _call_id(event.tool_use),
                {"label": "arguments", "value": json.dumps(args, ensure_ascii=False), "mono": True},
                {"label": "auth", "value": self.turn.gateway_auth},
                {"label": "gateway", "value": self.turn.gateway_id, "mono": True},
                {"label": "policy_engine", "value": self.turn.policy_engine_id, "mono": True},
                {"label": "policy_mode", "value": self.turn.policy_mode},
                {"label": "cedar_policy", "value": POLICY_FOR[name], "mono": True},
            ],
        }))
        self.started[event.tool_use["toolUseId"]] = time.monotonic()

    def _pin_arguments(self, kind: str, args: dict) -> None:
        pin_traveler_argument(args, self.turn.traveler_id)
        args["travelerConfirmed"] = self.turn.confirmed(kind)
        args["budgetCeilingCents"] = self.turn.budget_ceiling_cents
        args["journeyRef"] = f"concierge:{self.turn.conversation_id}"

    def _guarded(self, kind: str, tool_use: dict) -> None:
        """Record a governed write the Runtime declined because this turn already attempted it."""
        self.emit("activity", activity(
            "order",
            f"Second {kind} not sent: already attempted on this turn",
            f"This {kind} was already attempted on this turn, so the Runtime did not "
            "send the call again.",
            {
                "category": "runtime",
                "component": "Bedrock AgentCore Runtime, MeridianConcierge",
                "status": "error",
                "fields": [
                    {"label": "tool", "value": tool_use["name"], "mono": True},
                    _call_id(tool_use),
                    {
                        "label": "arguments",
                        "value": json.dumps(tool_use.get("input") or {}, ensure_ascii=False),
                        "mono": True,
                    },
                    {"label": "refused_by", "value": RUNTIME_GUARD},
                    {"label": "refusal_code", "value": WRITE_ALREADY_ATTEMPTED},
                ],
            },
        ))

    def after(self, event) -> None:
        name = short(event.tool_use["name"])
        if name not in SPANS:
            return
        tool_id = event.tool_use["toolUseId"]
        if tool_id not in self.started:
            return
        elapsed = round((time.monotonic() - self.started.pop(tool_id)) * 1000)
        result = event.result or {}
        payload = parse_result(result) if result.get("status") == "success" else None
        if payload is not None and (
            "error" not in payload or is_business_answer(name, payload)
        ):
            self._success(name, payload, elapsed, event.tool_use)
            return
        text = _error_text(result, payload)
        denied = payload is None and bool(DENIAL_PATTERN.match(text.strip()))
        summary = self._failure(
            name, text, denied, elapsed, event.tool_use,
            governance=(payload or {}).get("governance"),
        )
        # Hand the model the explained decision so its reply names the real reason;
        # the raw gateway text stays in the span for the trace viewer.
        event.result = {**result, "status": "error", "content": [{"text": summary}]}

    def _success(self, name: str, payload: dict, elapsed: int, tool_use: dict) -> None:
        if name in PACKAGE_SEARCH_TOOLS:
            self.packages = payload.get("packages") or []
            self.emit("packages", self.packages)
        kind = GOVERNED.get(name)
        if kind:
            self._settle(kind, payload.get(kind))
            self.emit(kind, {kind: payload.get(kind), "policyDecision": "allow"})
        summary = payload.get("summary") or _summary(name, payload)
        fields = [
            _call_id(tool_use),
            {"label": "result", "value": summary},
            {"label": "cedar_decision", "value": "allow"},
            {"label": "cedar_policy", "value": POLICY_FOR[name], "mono": True},
            {"label": "policy_mode", "value": self.turn.policy_mode},
        ]
        governance = payload.get("governance")
        if governance:
            fields.append(
                {"label": "workload", "value": governance.get("subject", ""), "mono": True}
            )
            fields.append({"label": "traveler_grant", "value": governance.get("decision", "")})
        self.emit("activity", activity(
            "search" if name in PACKAGE_SEARCH_TOOLS else "result",
            f"{name}: result",
            summary,
            {
                "category": "gateway",
                "component": "Bedrock AgentCore Gateway",
                "status": "ok",
                "fields": fields,
            },
            elapsed,
        ))

    def _failure(self, name, text, denied, elapsed, tool_use, *, governance=None) -> str:
        """Emit the failed span and the governed outcome; return the explained summary.

        A refusal by the holds Lambda's traveler grant carries the Lambda's decision and the
        deny row it kept, so the backend can name that layer from its own evidence.
        """
        args = tool_use.get("input") or {}
        kind = GOVERNED.get(name)
        if denied:
            summary = friendly_denial(text, args if kind else None, kind or "hold")
        else:
            summary = text or "The gateway returned no result."
        if denied and kind:
            title = f"{kind.capitalize()} refused by Cedar policy"
        elif denied:
            title = "Tool call denied by policy"
        else:
            title = "Tool call failed"
        self.emit("activity", activity("security" if denied else "error", title, summary, {
            "category": "security" if denied else "tool",
            "component": "Bedrock AgentCore Policy" if denied else "Bedrock AgentCore Gateway",
            "status": "denied" if denied else "error",
            "fields": [
                {"label": "tool", "value": tool_use["name"], "mono": True},
                _call_id(tool_use),
                {
                    "label": "arguments",
                    "value": json.dumps(tool_use.get("input") or {}, ensure_ascii=False),
                    "mono": True,
                },
                {"label": "gateway_error", "value": text, "mono": True},
            ] + ([
                {"label": "cedar_decision", "value": "deny"},
                {"label": "cedar_policy", "value": POLICY_FOR[name], "mono": True},
                {"label": "policy_mode", "value": self.turn.policy_mode},
            ] if denied else []) + _grant_fields(governance),
        }, elapsed))
        if kind:
            self._settle(kind, None)
            self.emit(kind, {
                kind: None,
                "refused": summary,
                "error": text,
                "policyDecision": "deny" if denied else None,
            })
        return summary
