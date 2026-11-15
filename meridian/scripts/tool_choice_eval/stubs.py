"""Stub tools: the Gateway's names and schemas, with canned results instead of Lambdas.

A stub answers from the seeded catalog (``scripts/travel_catalog.py``) so the model sees the
shape and the facts the real tools return. It runs nothing and writes nothing. Calls are not
recorded here: ``CallRecorder`` records at the hook layer, which also sees a call to a tool the
model was never offered.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Mapping

from strands.hooks import BeforeToolCallEvent, HookProvider
from strands.types._events import ToolResultEvent
from strands.types.tools import AgentTool, ToolSpec

from backend.agents.concierge_definition import bare_tool_name
from scripts.tool_choice_eval.scoring import ToolCall
from scripts.travel_catalog import TRIP_PACKAGES

MAX_CALLS_PER_TURN = 10
SHORTLIST = 3
RATES = {("USD", "EUR"): 0.92, ("USD", "JPY"): 151.0, ("EUR", "USD"): 1.09, ("USD", "GBP"): 0.79}
POLICY_DENIAL = (
    "AuthorizeActionException - Tool Execution Denied: Tool call not allowed due to policy "
    "enforcement [No policy applies to the request (denied by default).]"
)
LOYALTY = {"points": 48250, "tier": "Gold"}


def _row(package: Mapping[str, Any]) -> dict[str, Any]:
    keys = ("package_id", "name", "trip_type", "destination", "region", "price_per_person",
            "description", "durations")
    return {key: package[key] for key in keys}


def _words(*values: Any) -> set[str]:
    text = " ".join(str(v) for v in values if v).casefold()
    return {w.strip(".,!?'\"()") for w in text.split() if len(w) > 2}


def _matching(arguments: Mapping[str, Any]) -> list[dict[str, Any]]:
    wanted = _words(arguments.get("query"), arguments.get("text"), arguments.get("trip_type"))
    ceiling = arguments.get("max_price_per_person")
    trip_type = arguments.get("trip_type")
    rows = []
    for package in TRIP_PACKAGES:
        if trip_type and package["trip_type"].casefold() != str(trip_type).casefold():
            continue
        if isinstance(ceiling, (int, float)) and package["price_per_person"] > ceiling:
            continue
        haystack = _words(package["name"], package["destination"], package["region"],
                          package["description"], package["trip_type"])
        rows.append((len(wanted & haystack), package))
    rows.sort(key=lambda pair: -pair[0])
    return [_row(package) for _, package in rows[:SHORTLIST]]


def _by_id(package_id: Any) -> dict[str, Any] | None:
    return next((p for p in TRIP_PACKAGES if p["package_id"] == package_id), None)


def _search(arguments: Mapping[str, Any]) -> Any:
    return {"packages": _matching(arguments)}


def _compare(arguments: Mapping[str, Any]) -> Any:
    found = [_by_id(i) for i in arguments.get("package_ids") or []]
    return [{**_row(p), "availability": p["availability"]} for p in found if p]


def _price_range(arguments: Mapping[str, Any]) -> Any:
    place = str(arguments.get("destination", "")).casefold()
    prices = [p["price_per_person"] for p in TRIP_PACKAGES
              if place in p["destination"].casefold() or place in p["region"].casefold()]
    if not prices:
        return {"destination": arguments.get("destination"), "trips": 0}
    return {"destination": arguments.get("destination"), "trips": len(prices),
            "low": min(prices), "average": round(sum(prices) / len(prices), 2),
            "high": max(prices), "currency": "USD"}


def _region_inventory(arguments: Mapping[str, Any]) -> Any:
    region = str(arguments.get("region", "")).casefold()
    trip_type = str(arguments.get("trip_type") or "").casefold()
    rows = [p for p in TRIP_PACKAGES if region in p["region"].casefold()
            and trip_type in p["trip_type"].casefold()]
    return {"region": arguments.get("region"), "trip_type": arguments.get("trip_type"),
            "packages": len(rows), "places_left": sum(sum(p["availability"].values())
                                                      for p in rows)}


def _currency(arguments: Mapping[str, Any]) -> Any:
    pair = (str(arguments.get("from_currency", "")).upper(),
            str(arguments.get("to_currency", "")).upper())
    amount = arguments.get("amount")
    rate = RATES.get(pair)
    if rate is None or not isinstance(amount, (int, float)):
        return {"error": "unsupported_currency_pair", "final": False}
    return {"amount": amount, "from": pair[0], "to": pair[1], "rate": rate,
            "converted": round(amount * rate, 2), "indicative": True}


def _details(arguments: Mapping[str, Any]) -> Any:
    package = _by_id(arguments.get("packageId"))
    if package is None:
        return {"error": "package_not_found", "final": False}
    return {**_row(package), "availability": package["availability"],
            "highlights": package["highlights"]}


def _loyalty(arguments: Mapping[str, Any]) -> Any:
    return {"program": arguments.get("program"), **LOYALTY}


CANNED: dict[str, Callable[[Mapping[str, Any]], Any]] = {
    "search_trips": _search,
    "hybrid_trip_search": _search,
    "compare_packages": _compare,
    "price_range": _price_range,
    "region_inventory": _region_inventory,
    "currency_convert": _currency,
    "get_package_details": _details,
    "loyalty_balance": _loyalty,
}


def canned_result(name: str, arguments: Mapping[str, Any]) -> tuple[str, Any]:
    """The ``(status, payload)`` a stub returns. A write is refused as an unconfirmed turn's
    Cedar policy refuses it, because the platform pins ``travelerConfirmed`` to false there."""
    bare = bare_tool_name(name)
    if bare in CANNED:
        return "success", CANNED[bare](arguments)
    return "error", POLICY_DENIAL


class StubTool(AgentTool):
    """A Gateway tool by name and schema whose executor returns a canned result.

    Args:
        gateway_name: The name the model sees, ``Target___tool``.
        spec: The Strands tool spec for it (``surface.strands_spec``).
    """

    def __init__(self, gateway_name: str, spec: ToolSpec) -> None:
        super().__init__()
        self._name, self._spec = gateway_name, spec
        self.executions = 0

    @property
    def tool_name(self) -> str:
        return self._name

    @property
    def tool_spec(self) -> ToolSpec:
        return self._spec

    @property
    def tool_type(self) -> str:
        return "python"

    async def stream(self, tool_use, invocation_state, **kwargs):
        self.executions += 1
        arguments = tool_use.get("input") or {}
        status, payload = canned_result(self._name, arguments)
        if self.executions > MAX_CALLS_PER_TURN:
            status, payload = "error", {"error": "eval_call_limit", "final": True}
        text = payload if isinstance(payload, str) else json.dumps(payload)
        yield ToolResultEvent({
            "toolUseId": tool_use["toolUseId"], "status": status, "content": [{"text": text}],
        })


class CallRecorder(HookProvider):
    """Record every tool call the model makes, after the traveler pin has run.

    Register it after ``TravelerPinHook`` so the recorded ``travelerId`` is the pinned one. It
    also sees a call to a tool the agent does not have, which Strands answers with an error.
    """

    def __init__(self) -> None:
        self.calls: list[ToolCall] = []

    def register_hooks(self, registry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before)

    def before(self, event: BeforeToolCallEvent) -> None:
        arguments = event.tool_use.get("input")
        self.calls.append(ToolCall(
            name=bare_tool_name(event.tool_use["name"]),
            arguments=dict(arguments) if isinstance(arguments, dict) else {},
        ))
