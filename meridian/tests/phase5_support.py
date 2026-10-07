"""Test boundaries for the Phase 5 graph: retrieval, Gateway transport, lease store.

These replace only external services. The graph, the steps, the interrupts and
Strands' snapshot manager are real in every test that imports them.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.db.journey_store import ExecutionClaim

TOKYO = {"product_id": "pkg_tokyo", "name": "Tokyo", "price": 100,
         "available_sizes": ["7 nights"], "availability": {"7 nights": 10}}


async def fake_search(query: str, limit: int = 5):
    return [dict(TOKYO)], []


async def fake_availability(query: str, package_id: Optional[str] = None):
    return [{**TOKYO, "product_id": package_id or TOKYO["product_id"]}], [], ""


@dataclass
class GatewayFake:
    """The Gateway's ``tools/call``: records arguments, answers like the holds Lambda."""

    calls: List[Dict[str, Any]] = field(default_factory=list)
    receipts: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def __call__(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append({"tool": name, **arguments})
        replayed = arguments["holdRequestId"] in self.receipts
        receipt = self.receipts.setdefault(arguments["holdRequestId"], {
            "bookingId": arguments["bookingId"], "status": "held",
            "expiresAt": "2026-10-06T20:15:00Z", "createdAt": "2026-10-06T20:00:00Z",
            "observedAt": "2026-10-06T20:00:01Z",
        })
        hold = {**receipt, "replayed": replayed, "seatsRemaining": None if replayed else 9}
        return {"result": {"content": [{"type": "text", "text": json.dumps({"hold": hold})}]}}


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
