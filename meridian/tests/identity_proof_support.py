"""Recording fakes for the identity proof: a system that behaves as the design says it should."""

from __future__ import annotations

import json

from scripts.identity_probes.probes import (
    CONCIERGE,
    DECOY_TRAVELER,
    JORDAN_TRAVELER,
    TRAVELER_OF,
    Ports,
)
from scripts.identity_probes.receipt import DECOY

PACKAGE = {
    "package_id": "CTY-002", "name": "Test Package", "price_per_person": "1599.00",
    "availability": {"5 nights": 4, "7 nights": 0},
}
DIFFERENT_TRAVELER = "The request names a different traveler than the signed-in caller."
NOT_YOURS = "The authenticated caller is not authorized for that traveler."


class FakeCleanup:
    """A cleanup port that records what it was asked to remove."""

    def __init__(self, *, fail_purge=False, fail_release=False, leftovers=0, fail_check=False):
        self.purged, self.released, self.prefix, self.baseline = [], [], None, None
        self.order = []
        self.fail_purge, self.fail_release = fail_purge, fail_release
        self._leftovers, self.fail_check = leftovers, fail_check

    def purge_thread(self, thread):
        self.order.append("purge")
        if self.fail_purge:
            raise RuntimeError("purge refused for 123456789012")
        self.purged.append(thread)

    def release_bookings(self, bookings):
        self.order.append("release")
        if self.fail_release:
            raise RuntimeError("release failed")
        self.released.extend(bookings)
        return len(bookings)

    def leftovers(self, prefix, baseline):
        self.order.append("check")
        if self.fail_check:
            raise RuntimeError("cannot count")
        self.prefix, self.baseline = prefix, baseline
        return [f"leftover {n}" for n in range(self._leftovers)]


class FakeDatabase:
    """Row counts, deny rows and bookings a correct Aurora would report."""

    def __init__(self, *, rls_hides: bool = True, baseline: int = 9) -> None:
        self.rls_hides = rls_hides
        self.baseline = baseline
        self.deny_rows = 0
        self.bookings: dict[str, set[str]] = {JORDAN_TRAVELER: set(), DECOY_TRAVELER: set()}
        self.holds: dict[str, set[str]] = {}
        self.preflights = 0

    def add_booking(self, traveler: str, booking: str, ref: str | None = None) -> None:
        self.bookings[traveler].add(booking)
        if ref:
            self.holds.setdefault(ref, set()).add(booking)

    def preflight(self) -> None:
        self.preflights += 1

    def scoped_count(self, context_traveler: str, target_traveler: str) -> int:
        if context_traveler == target_traveler:
            return self.baseline
        return 0 if self.rls_hides else self.baseline

    def baseline_count(self, target_traveler: str) -> int:
        return self.baseline

    def deny_audit_count(self, traveler_id: str) -> int:
        return self.deny_rows

    def booking_ids(self, traveler_id: str) -> set[str]:
        return set(self.bookings[traveler_id])

    def hold_bookings(self, journey_ref: str) -> set[str]:
        return set(self.holds.get(journey_ref, set()))


def ticker():
    """A clock that advances ten milliseconds per reading."""
    state = {"now": 0.0}

    def clock() -> float:
        state["now"] += 0.01
        return state["now"]

    return clock


def text_result(payload: dict, *, is_error: bool = False) -> dict:
    """A tool result whose text content is ``payload`` as JSON."""
    result = {"content": [{"type": "text", "text": json.dumps(payload)}]}
    if is_error:
        result["isError"] = True
    return {"result": result}


def lambda_hold(booking: str) -> dict:
    """The Holds Lambda's success payload: the booking id sits under ``hold``."""
    return {"hold": {"bookingId": booking, "status": "held"}, "governance": {"allowed": True},
            "summary": f"Held CTY-002 (5 nights) for 1 traveler(s) as {booking}"}


def lambda_refusal() -> dict:
    """The Holds Lambda's refusal payload when the workload grant denies the traveler."""
    return {"error": "traveler_not_authorized", "governance": {"allowed": False}}


def fake_http(user, method, path, body):
    """A backend that refuses the decoy whenever a request names Jordan."""
    if path == "/api/me":
        return 200, {"traveler_id": TRAVELER_OF[user], "authentication": "cognito"}
    names_jordan = JORDAN_TRAVELER in path or (body or {}).get("traveler_id") == JORDAN_TRAVELER
    if user == DECOY and names_jordan:
        return 403, {"detail": NOT_YOURS}
    return 200, {"ok": True}


def fake_runtime(user, runtime_name, payload, limit):
    """Runtimes that refuse a payload naming a different traveler than the token's."""
    if user == DECOY and payload.get("traveler_id") == JORDAN_TRAVELER:
        return [{"type": "error", "code": "authorization", "message": DIFFERENT_TRAVELER}]
    if runtime_name == CONCIERGE:
        return [{"type": "activity", "name": "AgentCore Runtime: turn started"},
                {"type": "result", "message": "Hello."}]
    return [{"type": "heartbeat"}, {"type": "result", "state": {"workflow_status": "paused"}}]


def fake_gateway(database: FakeDatabase):
    """A Gateway whose Holds Lambda refuses the decoy and records a deny row."""

    def gateway(user, tool, arguments):
        if tool.endswith("get_package_details"):
            return text_result({"package": PACKAGE})
        if user == DECOY:
            database.deny_rows += 1
            return text_result(lambda_refusal(), is_error=True)
        database.add_booking(JORDAN_TRAVELER, "HLD-TEST0001", arguments["journeyRef"])
        return text_result(lambda_hold("HLD-TEST0001"))

    return gateway


def good_world(design: str = "both") -> tuple[Ports, FakeDatabase]:
    """Ports for a system in which every layer does its job."""
    database = FakeDatabase()
    ports = Ports(http=fake_http, runtime=fake_runtime, gateway=fake_gateway(database),
                  database=database, clock=ticker(), now=lambda: "2026-10-08T12:00:00+00:00")
    return ports, database
