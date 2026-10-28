"""One concierge definition for Phase 3 and the MeridianConcierge Runtime.

Phase 3 builds a local Strands agent from it; the Runtime builds the same agent with the
tools listed from the Gateway. It owns the tool contract (names and argument names), the
prompt core, the hook that pins the traveler, and the helpers that read tool results.

scripts/stage_concierge_runtime.py copies this file into the Runtime, which cannot import
``backend``. Import only the standard library and ``strands`` here; a test enforces it.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from strands import Agent
from strands.hooks import BeforeToolCallEvent, HookProvider

CATALOG_TOOLS = (
    "search_trips", "compare_packages", "price_range", "region_inventory",
    "currency_convert", "hybrid_trip_search",
)
DETAIL_TOOLS = ("get_package_details",)
TRAVELER_TOOLS = ("loyalty_balance", "create_courtesy_hold", "confirm_booking", "release_hold")
PHASE3_TOOLS = CATALOG_TOOLS + DETAIL_TOOLS + ("loyalty_balance",)
PHASE4_TOOLS = CATALOG_TOOLS + DETAIL_TOOLS + TRAVELER_TOOLS
# release_hold is a write but is not deployed until Release 3, so the Phase 4 prompt names
# only the two writes the Gateway lists today.
WRITE_TOOLS = ("create_courtesy_hold", "confirm_booking", "release_hold")
PACKAGE_SEARCH_TOOLS = ("search_trips", "hybrid_trip_search")
TRAVELER_ARGUMENT = "travelerId"
PHASES = (3, 4)

# The argument names every surface offers: the stdio server (Phases 2 and 3), the in-process
# tools (Phase 3) and the Gateway targets (Phase 4). tests/test_concierge_contract.py checks
# each surface against this table.
TOOL_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "search_trips": ("trip_type", "max_price_per_person", "limit", "text"),
    "compare_packages": ("package_ids",),
    "price_range": ("destination",),
    "region_inventory": ("region", "trip_type"),
    "currency_convert": ("amount", "from_ccy", "to_ccy"),
    "hybrid_trip_search": ("query", "limit"),
    "get_package_details": ("packageId",),
    "loyalty_balance": ("travelerId", "program"),
    "create_courtesy_hold": (
        "travelerId", "packageId", "duration", "travelers", "unitPriceCents", "totalCents",
        "holdMinutes", "travelerConfirmed", "budgetCeilingCents", "journeyRef",
        "holdRequestId", "bookingId", "executionId"),
    "confirm_booking": (
        "travelerId", "bookingId", "totalCents", "travelerConfirmed", "budgetCeilingCents",
        "journeyRef"),
    "release_hold": ("travelerId", "holdRequestId", "journeyId"),
}

PROMPT_CORE = (
    "You are Meridian's travel concierge for one authenticated traveler. Use only the tools "
    "you are given.\n"
    "\n"
    "Choose tools by the question:\n"
    "1. For trip discovery in the traveler's words (a mood, a place, a style), call "
    "hybrid_trip_search(query, limit=5) once. Focus the query on the destination and the "
    "positive attributes the traveler wants, such as boutique stays. Do not dilute it with "
    "date windows or negated dietary exclusions; assess those constraints against the "
    "returned facts. A price ceiling named in the query is applied for you, and sold-out "
    "trips are left out. Recommend at most two trips. If only one matches, say so.\n"
    "2. For a structured filter, a trip type or a price cap per person, call "
    "search_trips(trip_type, max_price_per_person, limit).\n"
    "3. To compare named packages side by side, call compare_packages(package_ids).\n"
    "4. For what trips cost in a place, call price_range(destination). For how many trips and "
    "departure places a region has, call region_inventory(region, trip_type). To convert "
    "prices, call currency_convert(amount, from_ccy, to_ccy); the rates are indicative.\n"
    "5. For durations and places left on one package, call get_package_details(packageId). Do "
    "this when the traveler asks about availability or a fact the answer needs is missing from "
    "the search results; independent detail checks may run together. A requested date window "
    "is not evidence of availability on those dates.\n"
    "6. For a points balance, call loyalty_balance(travelerId, program). The platform sets "
    "travelerId; never ask the traveler for it. When the tool reports no recorded balance, say "
    "so; never estimate points.\n"
    "\n"
    "A tool answer with \"final\": true, or the error traveler_not_authorized, is a refusal "
    "and it is final: tell the traveler plainly that you cannot help with that request, and "
    "do not call that tool again in this turn.\n"
    "\n"
    "Ground every statement in tool results and what the traveler says in this request. "
    "Never invent seats, prices, flight times or confirmations. Write two to four sentences "
    "for the "
    "traveler, no headings, no lists. Keep each trip's facts separate: never transfer a "
    "duration or lodging style from another trip. Quote the published per-traveler price, and "
    "leave party totals to the trip controls. Ask one useful next question instead of filling "
    "gaps with assumptions.\n"
    "Lead with the answer or a useful question. No \"Great!\", \"Perfect!\", \"Excellent!\", "
    "\"Absolutely\", \"Wonderful\", \"Fantastic\", \"Amazing\", \"Sure\", \"Certainly\", \"Of "
    "course\", or similar applause. Avoid flattery and unsupported superlatives. The interface "
    "shows progress; do not narrate routine searches.\n"
)

PHASE_RULES = {
    3: (
        "\n"
        "This is the Retrieval phase. You can read the catalog and the traveler's loyalty "
        "balance. You have no memory of earlier turns and no tool that holds or books. If the "
        "traveler asks to hold or book, say that holds and bookings run in Production, under "
        "policy, and offer the next read you can do.\n"
    ),
    4: (
        "\n"
        "In this phase, grounding also covers the authorized traveler context and remembered "
        "details, not only tool results and this request. For a "
        "comparison or preference follow-up, reuse the trips already discussed; do not repeat "
        "a search without a new need. When you search, do not dilute the query with every "
        "known traveler detail, and prioritize explicit preference matches.\n"
        "Mention the strongest saved-preference match when it applies. Use remembered details "
        "naturally, and never claim a preference was saved unless a tool confirmed that write.\n"
        "Your tools are served by AgentCore Gateway and every call is checked by Cedar policy "
        "before it runs. Two more tools write, and only when the turn says the traveler "
        "confirmed:\n"
        "7. create_courtesy_hold(...) only when the turn says the traveler has confirmed a "
        "hold. Pass the exact package, duration, travelers and prices from the tool results; "
        "the platform pins the traveler identity, the confirmation flag and the budget ceiling.\n"
        "8. confirm_booking(bookingId, totalCents) only when the turn says the traveler has "
        "confirmed the booking of a held package. It books catalog inventory in Meridian's "
        "database and takes no payment; the platform pins the same identity, confirmation flag "
        "and budget ceiling.\n"
        "\n"
        "If the gateway refuses a hold or a booking, that decision is final for the current "
        "turn only: do not call the same tool again in the same turn with different arguments. "
        "Tell the traveler exactly what the refusal said and what would make the action "
        "possible. Every new turn is a new decision: the confirmation, the party size and the "
        "budget ceiling can change between turns, so never assume an earlier refusal still "
        "applies.\n"
    ),
}


def short_name(tool_name: str) -> str:
    """The tool's contract name: ``tool_name`` with one ``Target___`` prefix removed.

    The rule of ``backend/tools/tool_names.bare_tool_name``, which this file cannot import:
    only the first separator is a prefix, and a name with no target before it or no tool after
    it is returned unchanged. tests/test_tool_names.py keeps the two in step.
    """
    target, separator, bare = tool_name.partition("___")
    return bare if separator and target and bare else tool_name


def system_prompt(phase: int) -> str:
    """The system prompt for a phase: one core, plus that phase's rules.

    Raises:
        ValueError: ``phase`` is not 3 or 4.
    """
    if phase not in PHASES:
        raise ValueError(f"phase {phase} has no concierge prompt; the phases are 3 or 4")
    return PROMPT_CORE + PHASE_RULES[phase]


def pin_traveler_argument(arguments: dict, traveler_id: str) -> None:
    """Set the traveler argument to the authorized traveler, whatever the model sent."""
    arguments[TRAVELER_ARGUMENT] = traveler_id


class ToolInputNotAnObject(ValueError):
    """A tool call that takes the traveler carried an input that is not a JSON object."""


class TravelerPinHook(HookProvider):
    """Overwrite ``travelerId`` on every tool that takes one.

    The traveler comes from the verified request, never from the model. The Gateway's request
    interceptor and Cedar enforce the same rule a second and third time in Phase 4.
    """

    def __init__(self, traveler_id: str) -> None:
        self.traveler_id = traveler_id

    def register_hooks(self, registry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before)

    def before(self, event: BeforeToolCallEvent) -> None:
        name = short_name(event.tool_use["name"])
        if TRAVELER_ARGUMENT not in TOOL_ARGUMENTS.get(name, ()):
            return
        arguments = event.tool_use.get("input")
        if arguments is None:
            arguments = event.tool_use["input"] = {}
        if not isinstance(arguments, dict):
            raise ToolInputNotAnObject(
                f"Refused the call to {name}: its input is not an object "
                f"(got {type(arguments).__name__}), so the verified traveler cannot be set."
            )
        pin_traveler_argument(arguments, self.traveler_id)


def tool_payload(result: dict) -> Any:
    """The JSON value a tool result carries, or None when it carries none.

    Reads structured content (unwrapping FastMCP's ``{"result": ...}`` envelope for lists),
    then JSON text blocks joined together, then a JSON block.
    """
    structured = result.get("structuredContent")
    if structured is not None:
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return structured["result"]
        return structured
    blocks = [b for b in result.get("content") or [] if isinstance(b, dict)]
    text = "".join(b["text"] for b in blocks if isinstance(b.get("text"), str))
    if text:
        try:
            return json.loads(text)
        except ValueError:
            return None
    for block in blocks:
        if "json" in block:
            return block["json"]
    return None


def package_rows(tool: str, payload: Any) -> list[dict]:
    """The package rows a search or compare result carries; each has a ``package_id``."""
    if tool in PACKAGE_SEARCH_TOOLS and isinstance(payload, dict):
        rows = payload.get("packages")
    elif tool == "compare_packages" and isinstance(payload, list):
        rows = payload
    else:
        rows = None
    return [dict(row) for row in rows or [] if isinstance(row, dict) and row.get("package_id")]


def build_agent(
    *,
    model: Any,
    tools: Iterable[Any],
    hooks: Iterable[Any],
    phase: int,
    extra_instructions: str = "",
    **options: Any,
) -> Agent:
    """Build the concierge agent for a phase.

    Args:
        model: the Strands model.
        tools: the tools for this surface (local, stdio MCP or Gateway MCP).
        hooks: hook providers; the caller adds the traveler pin and its trace hooks.
        phase: 3 or 4.
        extra_instructions: per-turn text appended to the phase prompt.
        **options: forwarded to ``Agent`` (``session_manager``, ``callback_handler``).

    Raises:
        ValueError: ``phase`` is not 3 or 4.
    """
    prompt = system_prompt(phase)
    if extra_instructions:
        prompt = f"{prompt}\n{extra_instructions}"
    return Agent(
        model=model, system_prompt=prompt, tools=list(tools), hooks=list(hooks), **options)
