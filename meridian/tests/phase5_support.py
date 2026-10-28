"""Test boundaries for the Phase 5 graph: retrieval, Gateway transport, lease store.

These replace only external services. The graph, the steps, the interrupts and
Strands' snapshot manager are real in every test that imports them.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.agents.phase_05_workflow.governed_hold import RELEASE_TOOL
from backend.db.journey_store import ExecutionClaim

TOKYO = {"product_id": "pkg_tokyo", "name": "Tokyo", "price": 100,
         "available_sizes": ["7 nights"], "availability": {"7 nights": 10}}


async def fake_search(query: str, limit: int = 5):
    return [dict(TOKYO)], []


async def fake_availability(query: str, package_id: Optional[str] = None):
    return [{**TOKYO, "product_id": package_id or TOKYO["product_id"]}], [], ""


CEDAR_DENIAL = (
    "AuthorizeActionException - Tool Execution Denied: Tool call not allowed due to policy "
    "enforcement [No policy applies to the request (denied by default).]"
)


@dataclass
class GatewayFake:
    """The Gateway's ``tools/call``: records arguments, answers like the holds Lambda.

    Like the Cedar policy in front of the real Gateway, it refuses a hold unless the
    arguments carry ``travelerConfirmed: true`` and a ``totalCents`` within
    ``budgetCeilingCents``. The refusal is the Gateway's tool-result denial envelope; it is
    recorded in ``calls`` and ``denied`` and creates no receipt. ``receipts`` stands for the
    holds Aurora committed, and ``committed_hold`` reads them the way the hold step reads
    ``hold_requests``.
    """

    calls: List[Dict[str, Any]] = field(default_factory=list)
    denied: List[Dict[str, Any]] = field(default_factory=list)
    receipts: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    releases: List[Dict[str, Any]] = field(default_factory=list)
    refuse_release: bool = False
    release_error: Optional[BaseException] = None
    lapsed_leases: bool = False
    journey_guard: bool = False
    garbled_release: bool = False
    replayed_releases: int = 0

    def __call__(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append({"tool": name, **arguments})
        if name == RELEASE_TOOL:
            return self._release(arguments)
        within_budget = arguments.get("totalCents", 0) <= arguments.get("budgetCeilingCents", 0)
        if arguments.get("travelerConfirmed") is not True or not within_budget:
            self.denied.append({"tool": name, **arguments})
            return {"result": {"isError": True, "content": [
                {"type": "text", "text": CEDAR_DENIAL},
            ]}}
        replayed = arguments["holdRequestId"] in self.receipts
        receipt = self.receipts.setdefault(arguments["holdRequestId"], {
            "bookingId": arguments["bookingId"], "status": "held",
            "expiresAt": "2026-10-06T20:15:00Z", "createdAt": "2026-10-06T20:00:00Z",
            "observedAt": "2026-10-06T20:00:01Z",
        })
        hold = {**receipt, "replayed": replayed, "seatsRemaining": None if replayed else 9}
        return {"result": {"content": [{"type": "text", "text": json.dumps({"hold": hold})}]}}

    def _release(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Answer release_hold like Cedar and the holds Lambda do."""
        self.releases.append(dict(arguments))
        if self.release_error is not None:
            raise self.release_error
        if self.refuse_release:
            return {"result": {"isError": True, "content": [
                {"type": "text", "text": CEDAR_DENIAL},
            ]}}
        if self.garbled_release:
            return {"result": {"content": [{"type": "text", "text": "not json"}]}}
        receipt = self.receipts.get(arguments.get("holdRequestId"))
        if self.lapsed_leases and arguments.get("executionId"):
            body = {"error": "execution_lease_lost"}
        elif self.journey_guard and arguments.get("executionId"):
            body = {"error": "journey_not_owned"}
        elif receipt is None:
            body = {"error": "hold_not_found"}
        elif receipt["status"] not in ("held", "released"):
            body = {"error": "booking_not_held"}
        else:
            replayed = receipt["status"] == "released"
            self.replayed_releases += int(replayed)
            receipt["status"] = "released"
            body = {"release": {
                "bookingId": receipt["bookingId"], "status": "released", "replayed": replayed,
                "holdRequestId": arguments["holdRequestId"], "journeyId": arguments["journeyId"],
            }}
        return {"result": {"content": [{"type": "text", "text": json.dumps(body)}]}}

    async def committed_hold(
        self, traveler_id: str, thread_id: str, journey_id: str, intent: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """The hold this request committed, shaped like the hold step's Aurora read."""
        receipt = self.receipts.get(str(intent.get("hold_request_id")))
        if receipt is None:
            return None
        return {
            "booking_id": receipt["bookingId"], "status": receipt["status"],
            "expires_at": receipt["expiresAt"], "created_at": receipt["createdAt"],
            "observed_at": receipt["observedAt"], "lapsed": False,
        }


@dataclass
class InMemoryLease:
    """The lease store's contract, kept in memory. Its SQL has its own live tests."""

    traveler_threads: Dict[str, str] = field(default_factory=dict)
    executions: List[Dict[str, Any]] = field(default_factory=list)
    busy: bool = False
    renewals_left: Optional[int] = None

    async def ensure_journey(self, traveler_id: str, thread_id: str) -> str:
        owner = self.traveler_threads.setdefault(thread_id, traveler_id)
        if owner != traveler_id:
            raise PermissionError("The workflow thread is already bound to another journey")
        return f"jrn_{thread_id}"

    async def claim(self, traveler_id, journey_id, thread_id, worker_id, lease_seconds):
        if self.busy:
            return ExecutionClaim(None, 0, worker_id, False, {"worker_id": "worker-other"})
        attempt = 1 + sum(1 for e in self.executions if e["thread_id"] == thread_id)
        execution_id = f"exe_{thread_id}_{attempt}"
        self.executions.append({"execution_id": execution_id, "thread_id": thread_id,
                                "worker_id": worker_id, "status": "running"})
        return ExecutionClaim(execution_id, attempt, worker_id, True, None)

    async def renew(self, traveler_id, execution_id, lease_seconds) -> bool:
        if self.renewals_left is None:
            return True
        self.renewals_left -= 1
        return self.renewals_left >= 0

    async def release(self, traveler_id, journey_id, execution_id, status) -> None:
        for execution in self.executions:
            if execution["execution_id"] == execution_id:
                execution["status"] = status

    async def previous_worker(self, traveler_id, thread_id, execution_id) -> Optional[str]:
        earlier = [e for e in self.executions
                   if e["thread_id"] == thread_id and e["execution_id"] != execution_id]
        return earlier[-1]["worker_id"] if earlier else None
