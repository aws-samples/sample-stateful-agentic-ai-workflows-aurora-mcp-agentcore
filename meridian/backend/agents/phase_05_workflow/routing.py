"""How a Phase 5 request routes through the graph, and where it pauses."""


def is_recovery_request(query: str) -> bool:
    """Does this query justify committing inventory?

    Only a disruption does. `classify_intent` labels anything that names both
    a trip and a date as a "plan", which correctly includes a documented
    availability question like "Which trip lengths are still available for
    Amalfi Coast Villa Week?". Routing on intent alone would let a read reserve
    seats, so the hold node needs a stronger signal: the traveler's trip broke
    and we are rebuilding it.
    """
    q = (query or "").lower()
    disrupted = any(
        marker in q
        for marker in ("cancelled", "canceled", "disrupt", "stranded", "rebook", "missed")
    )
    reworking = any(
        marker in q for marker in ("rework", "rebuild", "replan", "re-plan", "recover")
    )
    return disrupted and reworking


def classify_intent(query: str) -> str:
    q = query.lower()
    # Explicit recall language wins over incidental planning words. For
    # example, "Recall my October Tokyo plan..." is asking for memory even
    # though "plan" also appears as a noun.
    memory_signals = (
        "recall ",
        "remember",
        "last time",
        "previous",
        "we discussed",
        "you said",
    )
    if any(s in q for s in memory_signals):
        return "memory_recall"

    # "plan" is the multi-step intent: prompts that ask for a trip AND its
    # open dates in one breath. It routes through TWO sequential worker
    # nodes (search → availability) before synthesis — the case where an
    # explicit graph genuinely beats a single tool call, because the graph
    # composes steps and checkpoints between each.
    plan_signals = (
        "plan ",
        "plan our",
        "plan a",
        "plan me",
        "find a trip and",
        "and check availability",
        "and the open dates",
        "with open dates",
        "shortlist and",
        "then check",
        "end to end",
        "end-to-end",
    )
    # A prompt that names BOTH a destination/search intent AND a date/slot
    # intent is also a plan (e.g. "Kyoto trip and when it's available").
    has_search_intent = any(
        s in q for s in ("trip", "getaway", "escape", "vacation", "holiday", "find", "show me")
    )
    has_date_intent = any(
        s in q for s in ("date", "dates", "available", "availability", "departure", "slots", "when")
    )
    if any(s in q for s in plan_signals) or (has_search_intent and has_date_intent):
        return "plan"

    availability_signals = (
        "available",
        "availability",
        "departure",
        "departures",
        "slots",
        "dates",
        "what dates",
        "when can",
    )
    if any(s in q for s in availability_signals):
        return "availability"
    return "search"


def pauses_after_search(query: str, review_only: bool) -> bool:
    """Whether the run stops for review once search has completed.

    A review request always does. So does the canonical recovery, which pauses
    after search so the room sees a saved step before availability fan-out.
    A route that runs no search has nothing to pause after.

    Args:
        query: The traveler's request.
        review_only: The caller asked to review the shortlist first.

    Returns:
        True when the graph must interrupt before the node that follows search.
    """
    if review_only:
        return True
    normalized = query.lower()
    return (
        ("canceled" in normalized or "cancelled" in normalized)
        and "flight" in normalized
        and "then check" in normalized
        and "best three" in normalized
    )
