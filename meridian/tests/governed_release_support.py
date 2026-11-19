"""An in-memory Aurora and Gateway for the cleanup tests: holds, bookings and audit rows.

The booking tables force row level security, so a booking shows only inside a transaction pinned
to its traveler and the booking agent. Every statement is recorded, so a test can assert that a
cleanup issued no DELETE, and the audit rows are kept in a table a test can read afterwards.
"""

import json
import re

from scripts import governed_release

JORDAN = "trv_meridian_demo"


class FakeAurora:
    """Journeys, threads, hold requests, bookings and ``booking_action_audit`` rows."""

    def __init__(self, bookings, *, threads=None, audit=None, owner=JORDAN):
        """``bookings`` maps booking id to a ``(status, journey_id)`` pair."""
        self.status = {booking: state for booking, (state, _journey) in bookings.items()}
        self.journey = {booking: journey for booking, (_state, journey) in bookings.items()}
        self.threads = dict(threads or {})
        self.audit = list(audit or [])
        self.owner = owner
        self.pinned: dict[str, dict] = {}
        self.statements: list[tuple[str, tuple]] = []

    def begin_transaction(self):
        self.pinned[f"tx{len(self.pinned)}"] = {}
        return list(self.pinned)[-1]

    def commit_transaction(self, tx):
        pass

    def rollback_transaction(self, tx):
        pass

    def deletes(self):
        return [sql for sql, _params in self.statements
                if sql.lstrip().upper().startswith(("DELETE", "UPDATE", "INSERT"))]

    def hold(self, booking):
        return {"booking_id": booking, "hold_request_id": f"hrq-{booking}",
                "journey_id": self.journey[booking]}

    async def execute(self, sql, params=(), transaction_id=None, **_kwargs):
        self.statements.append((sql, tuple(params)))
        if "set_config" in sql:
            names = re.findall(r"set_config\('([\w.]+)'", sql)
            self.pinned[transaction_id].update(zip(names, params, strict=True))
            return [{}]
        if sql.startswith("DELETE"):
            return []
        if sql == governed_release.ACTIVE_SQL:
            return self._active(params[0], transaction_id)
        return self._reads(sql, params)

    def _reads(self, sql, params):
        by_journey = [self.hold(b) for b in sorted(self.journey) if self.journey[b] == params[0]]
        if sql == governed_release.RUN_HOLDS_SQL:
            journey = self.threads.get(params[0])
            return [self.hold(b) for b in sorted(self.journey) if self.journey[b] == journey]
        if sql == governed_release.JOURNEY_HOLDS_SQL:
            return by_journey
        if sql == governed_release.THREADS_OF_JOURNEY_SQL:
            return [{"n": sum(1 for j in self.threads.values() if j == params[0])}]
        if sql == governed_release.BOOKING_HOLD_SQL:
            return [self.hold(params[0])] if params[0] in self.journey else []
        if "FROM journey_threads WHERE thread_id IN" in sql:
            return [{"journey_id": self.threads[t], "thread_id": t}
                    for t in params if t in self.threads]
        raise AssertionError(f"unexpected statement: {sql}")

    def _active(self, booking, tx):
        scope = self.pinned.get(tx) or {}
        if (scope.get("app.current_traveler_id"), scope.get("app.agent_type")) != (
                self.owner, "booking_agent") or booking not in self.status:
            return []
        return [{"active": self.status[booking] == "held"}]


class FakeGateway:
    """The governed release tool: a release turns a held booking into a released one."""

    def __init__(self, db):
        self.db, self.calls = db, []

    def call_tool(self, name, args):
        booking = args["holdRequestId"].removeprefix("hrq-")
        self.calls.append(booking)
        self.db.status[booking] = "released"
        body = json.dumps({"release": {"status": "released", "replayed": False}})
        return {"result": {"content": [{"type": "text", "text": body}]}}


def install_gateway(monkeypatch, db):
    made = FakeGateway(db)
    monkeypatch.setattr("backend.agentcore.gateway.get_agentcore_gateway", lambda: made)
    return made
