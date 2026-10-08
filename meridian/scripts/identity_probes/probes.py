"""The probe plan: one request as one user, classified into a verdict that names the layer.

Every network or database effect goes through ``Ports``, so the plan runs against fakes in unit
tests and against the released system through ``effects.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol

from scripts.identity_probes.classify import (
    Verdict,
    classify_backend,
    classify_database_decoy,
    classify_database_jordan,
    classify_gateway,
    classify_runtime,
    gateway_shape,
    tool_payload,
)
from scripts.identity_probes.receipt import ALLOWED, DECOY, ERROR, JORDAN, REFUSED, Outcome, scrub

JORDAN_TRAVELER = "trv_meridian_demo"
DECOY_TRAVELER = "trv_demo_decoy"
TRAVELER_OF = {JORDAN: JORDAN_TRAVELER, DECOY: DECOY_TRAVELER}
THREAD_PREFIX = "phase5-proof-idp"
PACKAGE_ID = "CTY-002"
DETAILS_TOOL = "MeridianHolds___get_package_details"
HOLD_TOOL = "MeridianHolds___create_courtesy_hold"
WORKFLOW, CONCIERGE = "workflow", "concierge"
QUERY = "My JFK-to-Tokyo flight was canceled. Rework the trip."
HOLD_MINUTES = 15
CEILING_MARGIN_CENTS = 100_000
RUNTIME_EVENT_LIMIT = 60
CONCIERGE_EVENT_LIMIT = 5000
TRAVELERS = (JORDAN_TRAVELER, DECOY_TRAVELER)
AUDIT_RESIDUE = (
    "audit rows (traveler_access_audit, the agent audit log) are append-only and stay")
MEMORY_RESIDUE = (
    f"AgentCore Memory events written for actor {JORDAN_TRAVELER} by the Concierge turn are not "
    "deleted by this command; they age out under the memory's event expiry")
BookingRef = tuple[str, str]


class DatabasePort(Protocol):
    """What the proof reads from Aurora."""

    def scoped_count(self, context_traveler: str, target_traveler: str) -> int:
        """Rows of ``target_traveler`` that the app role sees with ``context_traveler`` pinned."""

    def preflight(self) -> None:
        """Raise when a table the proof reads without a scope would hide rows from the role."""

    def baseline_count(self, target_traveler: str) -> int:
        """Rows that exist for ``target_traveler``, counted with that traveler pinned."""

    def deny_audit_count(self, traveler_id: str) -> int:
        """All deny rows in ``traveler_access_audit`` that name ``traveler_id``."""

    def booking_ids(self, traveler_id: str) -> set[str]:
        """The booking ids ``traveler_id`` owns, read with the traveler and booking agent pinned."""

    def hold_bookings(self, journey_ref: str) -> set[str]:
        """The booking ids the Holds Lambda recorded for the journey reference."""


@dataclass(frozen=True)
class Ports:
    """Everything the plan can touch."""

    http: Callable[[str, str, str, dict | None], tuple[int, Any]]
    runtime: Callable[[str, str, dict, int], list[dict]]
    gateway: Callable[[str, str, dict], Any]
    database: DatabasePort
    clock: Callable[[], float]
    now: Callable[[], str]


@dataclass
class Context:
    """State shared by the probes of one run.

    Attributes:
        run_id: Eight hex characters that make this run's threads unique.
        design: The Gateway enforcement that shipped (``both``, ``cedar`` or ``interceptor``).
        package: The package details Jordan read, used to build catalog-true holds.
        threads: Every journey or thread name the run may have created, for the purge.
        owned_bookings: ``(traveler, booking)`` pairs the run provably made, for the release.
        baseline: Each traveler's booking ids before the first probe, for the leftover check.
        memory_sessions: Concierge session ids sent, whose Memory events stay behind.
    """

    run_id: str
    design: str
    package: dict[str, Any] = field(default_factory=dict)
    threads: list[str] = field(default_factory=list)
    owned_bookings: set[BookingRef] = field(default_factory=set)
    baseline: dict[str, frozenset[str]] | None = None
    memory_sessions: list[str] = field(default_factory=list)

    def thread(self, label: str) -> str:
        """A thread name unique to this run, remembered for the purge."""
        name = f"{THREAD_PREFIX}{self.run_id}{label}"
        if name in self.threads:
            raise ValueError(f"the thread label {label!r} was used twice in one run")
        self.threads.append(name)
        return name

    def session(self, label: str) -> str:
        """A Concierge conversation id unique to this run, remembered for the residue note."""
        name = f"idproof-{self.run_id}{label}"
        self.memory_sessions.append(name)
        return name

    def residue_notes(self) -> list[str]:
        """What the run leaves behind on purpose, for the receipt."""
        notes = [f"Residue: {AUDIT_RESIDUE}."]
        if self.memory_sessions:
            notes.append(f"AgentCore Memory, session id(s) {', '.join(self.memory_sessions)}: "
                         f"{MEMORY_RESIDUE}.")
        return notes


def snapshot_bookings(ports: Ports) -> dict[str, frozenset[str]]:
    """Both travelers' booking ids, each read with the traveler and booking agent pinned."""
    return {traveler: frozenset(ports.database.booking_ids(traveler)) for traveler in TRAVELERS}


@dataclass
class BookingWindow:
    """The bookings that appeared while one call ran.

    Attributes:
        payload_ids: Booking ids the call's own answer named; set by the probe.
        owned: Bookings that appeared and are tied to this call by its answer or its journey
            reference, so the run may release them.
        untied: Bookings that appeared but cannot be tied to this call. They are reported and
            never released.
    """

    payload_ids: set[str] = field(default_factory=set)
    owned: set[BookingRef] = field(default_factory=set)
    untied: set[BookingRef] = field(default_factory=set)

    @property
    def appeared(self) -> set[BookingRef]:
        """Every booking that appeared during the window."""
        return self.owned | self.untied


@contextmanager
def watch_bookings(ports: Ports, ctx: Context, journey_ref: str | None) -> Iterator[BookingWindow]:
    """Snapshot both travelers' bookings around a call and classify what appeared.

    The diff runs even when the call raises, so a booking made by a call whose answer never
    arrived is still found. A new booking is released later only when the call's answer or the
    journey reference ties it to this call; any other new booking is left alone and reported.
    """
    window = BookingWindow()
    before = snapshot_bookings(ports)
    try:
        yield window
    finally:
        after = snapshot_bookings(ports)
        tied = set(window.payload_ids)
        if journey_ref:
            tied |= ports.database.hold_bookings(journey_ref)
        for traveler in TRAVELERS:
            for booking in after[traveler] - before[traveler]:
                target = window.owned if booking in tied else window.untied
                target.add((traveler, booking))
        ctx.owned_bookings |= window.owned


def _payload_booking_ids(raw: Any) -> set[str]:
    booking = tool_payload(raw).get("bookingId")
    return {booking} if isinstance(booking, str) else set()


def _order_booking_ids(body: Any) -> set[str]:
    order = body.get("order") if isinstance(body, dict) else None
    order_id = order.get("order_id") if isinstance(order, dict) else None
    return {order_id} if isinstance(order_id, str) else set()


def _appeared_error(window: BookingWindow) -> Verdict:
    names = sorted(f"{booking} ({traveler})" for traveler, booking in window.appeared)
    return Verdict(ERROR, None, f"a booking appeared during the call: {', '.join(names)}")


ProbeFn = Callable[[Ports, Context], tuple[Verdict, dict[str, Any]]]


@dataclass(frozen=True)
class ProbeSpec:
    """One probe: who sends what, what is expected, and the function that runs it."""

    id: str
    layer: str
    actor: str
    expected: str
    sends: str
    run: ProbeFn


def build_hold_arguments(
    package: dict[str, Any], traveler_id: str, journey_ref: str
) -> dict[str, Any]:
    """Catalog-true courtesy hold arguments for one traveler.

    Raises:
        ValueError: When the package was not read, has no duration with open places or has no
            usable price.
    """
    if not package:
        raise ValueError("the package details were not read, so no catalog-true hold is built")
    open_durations = [name for name, places in (package.get("availability") or {}).items()
                      if isinstance(places, int) and places > 0]
    if not open_durations:
        raise ValueError(f"package {package.get('package_id')} has no duration with open places")
    try:
        price = Decimal(str(package["price_per_person"]))
    except (KeyError, ArithmeticError) as exc:
        raise ValueError("the package has no usable price_per_person") from exc
    unit = int((price * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return {
        "travelerId": traveler_id, "packageId": str(package["package_id"]),
        "duration": open_durations[0], "travelers": 1, "unitPriceCents": unit,
        "totalCents": unit, "holdMinutes": HOLD_MINUTES, "travelerConfirmed": True,
        "budgetCeilingCents": unit + CEILING_MARGIN_CENTS, "journeyRef": journey_ref,
    }


def _decoy_reads_jordan_memory(ports: Ports, ctx: Context):
    status, body = ports.http(DECOY, "GET", f"/api/memory/{JORDAN_TRAVELER}", None)
    return classify_backend(status, body), {"http_status": status}


def _decoy_orders_for_jordan(ports: Ports, ctx: Context):
    order = {"product_id": PACKAGE_ID, "quantity": 1, "phase": 4, "traveler_id": JORDAN_TRAVELER}
    with watch_bookings(ports, ctx, None) as window:
        status, body = ports.http(DECOY, "POST", "/api/order", order)
        window.payload_ids = _order_booking_ids(body)
    verdict = _appeared_error(window) if window.appeared else classify_backend(status, body)
    return verdict, {"http_status": status, "bookings_appeared": len(window.appeared)}


def _me(user: str) -> ProbeFn:
    def probe(ports: Ports, ctx: Context):
        status, body = ports.http(user, "GET", "/api/me", None)
        expected = TRAVELER_OF[user]
        got = body.get("traveler_id") if isinstance(body, dict) else None
        evidence = {"http_status": status}
        if status == 200 and got == expected:
            return Verdict(ALLOWED, None, f"HTTP 200, signed in as {expected}"), evidence
        return Verdict(ERROR, None, scrub(f"HTTP {status}, traveler {got}")), evidence

    return probe


def _jordan_reads_own_memory(ports: Ports, ctx: Context):
    status, body = ports.http(JORDAN, "GET", "/api/memory/me", None)
    return classify_backend(status, body), {"http_status": status}


def _decoy_sees_jordan_rows(ports: Ports, ctx: Context):
    visible = ports.database.scoped_count(DECOY_TRAVELER, JORDAN_TRAVELER)
    baseline = ports.database.baseline_count(JORDAN_TRAVELER)
    return classify_database_decoy(visible, baseline), {"visible": visible, "baseline": baseline}


def _jordan_sees_own_rows(ports: Ports, ctx: Context):
    visible = ports.database.scoped_count(JORDAN_TRAVELER, JORDAN_TRAVELER)
    baseline = ports.database.baseline_count(JORDAN_TRAVELER)
    return classify_database_jordan(visible, baseline), {"visible": visible, "baseline": baseline}


def _runtime_payload(runtime: str, mode: str, label: str, ctx: Context) -> dict[str, Any]:
    if runtime == WORKFLOW and mode == "ping":
        return {"event": "workflow_turn", "mode": "ping"}
    if runtime == WORKFLOW:
        return {"event": "workflow_turn", "mode": "start", "thread_id": ctx.thread(label),
                "traveler_id": JORDAN_TRAVELER, "query": QUERY, "travelers_count": 1,
                "review_only": True}
    return {"event": "concierge_turn", "prompt": "Say hello in one short sentence.",
            "conversation_id": ctx.session(label), "traveler_id": JORDAN_TRAVELER}


def _runtime(user: str, runtime: str, mode: str, label: str) -> ProbeFn:
    def probe(ports: Ports, ctx: Context):
        payload = _runtime_payload(runtime, mode, label, ctx)
        limit = CONCIERGE_EVENT_LIMIT if runtime == CONCIERGE else RUNTIME_EVENT_LIMIT
        events = ports.runtime(user, runtime, payload, limit)
        return classify_runtime(events), {"events": len(events)}

    return probe


def _reads_package(user: str) -> ProbeFn:
    def probe(ports: Ports, ctx: Context):
        raw = ports.gateway(user, DETAILS_TOOL, {"packageId": PACKAGE_ID})
        verdict = classify_gateway(raw, deny_rows=0, design=ctx.design)
        package = tool_payload(raw).get("package")
        if user == JORDAN and verdict.result == ALLOWED and isinstance(package, dict):
            ctx.package = package
        return verdict, {"package": PACKAGE_ID}

    return probe


def _decoy_holds_for_jordan(ports: Ports, ctx: Context):
    journey_ref = ctx.thread("hd")
    arguments = build_hold_arguments(ctx.package, JORDAN_TRAVELER, journey_ref)
    audit_before = ports.database.deny_audit_count(DECOY_TRAVELER)
    with watch_bookings(ports, ctx, journey_ref) as window:
        raw = ports.gateway(DECOY, HOLD_TOOL, arguments)
        window.payload_ids = _payload_booking_ids(raw)
    delta = ports.database.deny_audit_count(DECOY_TRAVELER) - audit_before
    verdict = classify_gateway(raw, deny_rows=delta, design=ctx.design)
    if window.appeared:
        verdict = _appeared_error(window)
    evidence = {"deny_audit_rows": delta, "design": ctx.design,
                "bookings_appeared": len(window.appeared), "shape": gateway_shape(raw)}
    return verdict, evidence


def _jordan_places_hold(ports: Ports, ctx: Context):
    journey_ref = ctx.thread("hj")
    arguments = build_hold_arguments(ctx.package, JORDAN_TRAVELER, journey_ref)
    with watch_bookings(ports, ctx, journey_ref) as window:
        raw = ports.gateway(JORDAN, HOLD_TOOL, arguments)
        window.payload_ids = _payload_booking_ids(raw)
    verdict = classify_gateway(raw, deny_rows=0, design=ctx.design)
    if window.untied:
        verdict = _appeared_error(window)
    elif verdict.result == ALLOWED and not window.owned:
        verdict = Verdict(ERROR, None, "the Gateway accepted the call but no booking exists "
                          "for it in Jordan's pinned scope")
    return verdict, {"bookings_created": len(window.owned),
                     "bookings_untied": len(window.untied)}


PLAN: tuple[ProbeSpec, ...] = (
    ProbeSpec("backend.decoy_reads_jordan_memory", "backend", DECOY, REFUSED,
              "GET /api/memory/<Jordan's traveler id> with the decoy's token",
              _decoy_reads_jordan_memory),
    ProbeSpec("backend.decoy_orders_for_jordan", "backend", DECOY, REFUSED,
              "POST /api/order naming Jordan's traveler id with the decoy's token",
              _decoy_orders_for_jordan),
    ProbeSpec("backend.decoy_me_is_decoy", "backend", DECOY, ALLOWED,
              "GET /api/me with the decoy's token: the decoy's own identity", _me(DECOY)),
    ProbeSpec("backend.jordan_me_is_jordan", "backend", JORDAN, ALLOWED,
              "GET /api/me with Jordan's token", _me(JORDAN)),
    ProbeSpec("backend.jordan_reads_own_memory", "backend", JORDAN, ALLOWED,
              "GET /api/memory/me with Jordan's token", _jordan_reads_own_memory),
    ProbeSpec("database.decoy_sees_jordan_rows", "database", DECOY, REFUSED,
              "Data API transaction: pin the decoy, step down to meridian_app, count Jordan's rows",
              _decoy_sees_jordan_rows),
    ProbeSpec("database.jordan_sees_own_rows", "database", JORDAN, ALLOWED,
              "the same transaction with Jordan pinned", _jordan_sees_own_rows),
    ProbeSpec("runtime.decoy_tampers_workflow", "runtime", DECOY, REFUSED,
              "MeridianWorkflow start with the decoy's token and Jordan's traveler in the payload",
              _runtime(DECOY, WORKFLOW, "turn", "wd")),
    ProbeSpec("runtime.decoy_tampers_concierge", "runtime", DECOY, REFUSED,
              "MeridianConcierge turn with the decoy's token and Jordan's traveler in the payload",
              _runtime(DECOY, CONCIERGE, "turn", "cd")),
    ProbeSpec("runtime.decoy_pings_workflow", "runtime", DECOY, ALLOWED,
              "MeridianWorkflow ping with the decoy's token: the decoy is let in",
              _runtime(DECOY, WORKFLOW, "ping", "dp")),
    ProbeSpec("runtime.jordan_pings_workflow", "runtime", JORDAN, ALLOWED,
              "MeridianWorkflow ping with Jordan's token", _runtime(JORDAN, WORKFLOW, "ping", "p")),
    ProbeSpec("runtime.jordan_runs_workflow", "runtime", JORDAN, ALLOWED,
              "MeridianWorkflow review-only start with Jordan's token (purged afterwards)",
              _runtime(JORDAN, WORKFLOW, "turn", "wj")),
    ProbeSpec("runtime.jordan_opens_concierge", "runtime", JORDAN, ALLOWED,
              "MeridianConcierge turn with Jordan's token, read to its result",
              _runtime(JORDAN, CONCIERGE, "turn", "cj")),
    ProbeSpec("gateway.jordan_reads_package", "gateway", JORDAN, ALLOWED,
              "tools/call get_package_details with Jordan's token", _reads_package(JORDAN)),
    ProbeSpec("gateway.decoy_reads_package", "gateway", DECOY, ALLOWED,
              "tools/call get_package_details with the decoy's token: the decoy is let in",
              _reads_package(DECOY)),
    ProbeSpec("gateway.decoy_holds_for_jordan", "gateway", DECOY, REFUSED,
              "tools/call create_courtesy_hold with the decoy's token and travelerId set to Jordan",
              _decoy_holds_for_jordan),
    ProbeSpec("gateway.jordan_places_hold", "gateway", JORDAN, ALLOWED,
              "tools/call create_courtesy_hold with Jordan's token (released afterwards)",
              _jordan_places_hold),
)


def select(jordan_only: bool) -> tuple[ProbeSpec, ...]:
    """The whole plan, or only the probes that send Jordan's token."""
    return tuple(spec for spec in PLAN if not jordan_only or spec.actor == JORDAN)


def run_probe(spec: ProbeSpec, ports: Ports, ctx: Context) -> Outcome:
    """Run one probe; an exception becomes an ``error`` outcome so the other probes still run."""
    started = ports.clock()
    evidence: dict[str, Any] = {}
    try:
        verdict, evidence = spec.run(ports, ctx)
    except Exception as exc:  # noqa: BLE001 - one failed probe must not hide the others
        verdict = Verdict(ERROR, None, f"{type(exc).__name__}: {exc}")
    return Outcome(
        probe=spec.id, layer=spec.layer, actor=spec.actor, expected=spec.expected,
        result=verdict.result, refused_by=verdict.refused_by, detail=scrub(verdict.detail),
        evidence=evidence, millis=int((ports.clock() - started) * 1000), at=ports.now(),
    )
