"""Trace hooks: every gateway tool call becomes a span the showcase can render.

The hooks also pin the hold contract to the turn. The model proposes a hold; the
traveler id, the confirmation flag, the budget ceiling and the journey reference
always come from the request the backend authorized, never from the model. The
gateway's Cedar policies then decide with those values.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider

AGENT_FILE = "meridian_agentcore/app/MeridianConcierge/main.py"
POLICY_REASONS = {
    "meridian_hold_governance": (
        "A courtesy hold runs only after the traveler confirms it, for 12 hours or less, "
        "for at most 6 travelers, and within the traveler's saved budget ceiling."
    ),
}
HOLD_LIMITS = {"holdMinutes": 720, "travelers": 6}
# The policy that must permit each tool. The engine is default deny with one permit
# per action, so a call that ran was permitted by this policy and a denied call
# found no permit in it. The gateway reports the decision, not the policy name.
POLICY_FOR = {
    "semantic_trip_search": "meridian_read_tools",
    "get_package_details": "meridian_read_tools",
    "create_courtesy_hold": "meridian_hold_governance",
}
SPANS = {
    "semantic_trip_search": (
        "search",
        "AgentCore Gateway · tools/call → semantic_trip_search",
        "Aurora pgvector search through the managed MCP tool",
    ),
    "get_package_details": (
        "inventory",
        "AgentCore Gateway · tools/call → get_package_details",
        "Live durations, availability and highlights for one package",
    ),
    "create_courtesy_hold": (
        "order",
        "AgentCore Gateway · tools/call → create_courtesy_hold",
        "Cedar decides on the arguments, then one atomic Aurora write",
    ),
}
DENIAL_PATTERN = re.compile(r"policy|denied|not authori[sz]ed|forbid", re.I)


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


def short(tool_name: str) -> str:
    return tool_name.split("___")[-1]


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


def hold_deny_reasons(args: dict) -> list[str]:
    """Name the hold conditions the arguments fail, in the order the policy states them."""
    reasons = []
    if args.get("travelerConfirmed") is not True:
        reasons.append("the traveler has not confirmed this hold")
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


def friendly_denial(text: str, args: dict | None = None) -> str:
    """Turn the gateway's policy error into a sentence; the raw text stays in the span."""
    named = re.search(r"denied due to ([A-Za-z0-9_]+?)(?:-[a-z0-9]+_?)?\]", text)
    if named:
        policy = named.group(1)
        return f"Refused by Cedar policy {policy}. {POLICY_REASONS.get(policy, '')}".strip()
    if "No policy applies" in text or "denied by default" in text:
        reasons = hold_deny_reasons(args) if args else []
        if reasons:
            return (
                "No Cedar policy permits this hold, so the gateway denied it by default: "
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
    if payload is not None and "error" in payload:
        return str(payload["error"])
    return " ".join(
        block.get("text", "") for block in result.get("content", []) if isinstance(block, dict)
    )[:600]


class TraceHooks(HookProvider):
    """Emit spans for every tool call and pin the hold arguments to the turn."""

    def __init__(self, queue, turn: TurnContext) -> None:
        self.queue = queue
        self.turn = turn
        self.started: dict[str, float] = {}
        self.hold: dict | None = None
        self.hold_settled = False
        self.packages: list[dict] = []

    def register_hooks(self, registry, **kwargs) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before)
        registry.add_callback(AfterToolCallEvent, self.after)

    def emit(self, kind: str, payload) -> None:
        self.queue.put_nowait((kind, payload))

    def before(self, event) -> None:
        name = short(event.tool_use["name"])
        if name not in SPANS:
            return
        args = event.tool_use.setdefault("input", {})
        if name == "create_courtesy_hold":
            if self.hold_settled:
                # The gateway already decided this hold on this turn. Retrying with
                # the same pinned arguments cannot change a Cedar decision.
                event.cancel_tool = (
                    "The gateway already decided this hold on this turn. Do not call "
                    "create_courtesy_hold again; explain the outcome to the traveler."
                )
                return
            self._pin_hold_arguments(args)
        kind, title, summary = SPANS[name]
        self.emit("activity", activity(kind, title, summary, {
            "category": "gateway",
            "component": "Bedrock AgentCore Gateway",
            "status": "ok",
            "fields": [
                {"label": "tool", "value": event.tool_use["name"], "mono": True},
                {"label": "arguments", "value": json.dumps(args, ensure_ascii=False), "mono": True},
                {"label": "auth", "value": "SigV4"},
                {"label": "gateway", "value": self.turn.gateway_id, "mono": True},
                {"label": "policy_engine", "value": self.turn.policy_engine_id, "mono": True},
                {"label": "policy_mode", "value": self.turn.policy_mode},
                {"label": "cedar_policy", "value": POLICY_FOR[name], "mono": True},
            ],
        }))
        self.started[event.tool_use["toolUseId"]] = time.monotonic()

    def _pin_hold_arguments(self, args: dict) -> None:
        args["travelerId"] = self.turn.traveler_id
        args["travelerConfirmed"] = self.turn.hold_confirmed
        args["budgetCeilingCents"] = self.turn.budget_ceiling_cents
        args["journeyRef"] = f"concierge:{self.turn.conversation_id}"

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
        if payload is not None and "error" not in payload:
            self._success(name, payload, elapsed)
            return
        text = _error_text(result, payload)
        summary = self._failure(name, text, bool(DENIAL_PATTERN.search(text)), elapsed, event.tool_use)
        # Hand the model the explained decision so its reply names the real reason;
        # the raw gateway text stays in the span for the audience.
        event.result = {**result, "status": "error", "content": [{"text": summary}]}

    def _success(self, name: str, payload: dict, elapsed: int) -> None:
        if name == "semantic_trip_search":
            self.packages = payload.get("packages") or []
            self.emit("packages", self.packages)
        if name == "create_courtesy_hold":
            self.hold = payload.get("hold")
            self.hold_settled = True
            self.emit("hold", {"hold": self.hold, "policyDecision": "allow"})
        summary = payload.get("summary") or f"{name} returned"
        fields = [
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
            "search" if name == "semantic_trip_search" else "result",
            f"{name} · result",
            summary,
            {
                "category": "gateway",
                "component": "Bedrock AgentCore Gateway",
                "status": "ok",
                "fields": fields,
            },
            elapsed,
        ))

    def _failure(self, name, text, denied, elapsed, tool_use) -> str:
        """Emit the failed span and the hold outcome; return the explained summary."""
        args = tool_use.get("input") or {}
        if denied:
            summary = friendly_denial(text, args if name == "create_courtesy_hold" else None)
        else:
            summary = text or "The gateway returned no result."
        if denied and name == "create_courtesy_hold":
            title = "Hold refused by Cedar policy"
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
            ] if denied else []),
        }, elapsed))
        if name == "create_courtesy_hold":
            self.hold = None
            self.hold_settled = True
            self.emit("hold", {
                "hold": None,
                "refused": summary,
                "error": text,
                "policyDecision": "deny" if denied else None,
            })
        return summary
