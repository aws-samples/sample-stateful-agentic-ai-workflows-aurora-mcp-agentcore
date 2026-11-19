"""A stop settles the execution it was issued against, never a replacement started after it."""

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException

from backend.agentcore.workflow_runtime import SessionStop, workflow_session_id
from backend.http_auth import HttpPrincipal
from backend.routers import journeys

JORDAN = HttpPrincipal("test", "trv_meridian_demo", "test")
JOURNEY, THREAD = "jrn_1", "thread_1"


@dataclass
class FakeAurora:
    """The journey tables the stop reads and writes, answering the router's own SQL."""

    executions: List[Dict[str, Any]] = field(default_factory=list)
    stops: List[Dict[str, Any]] = field(default_factory=list)
    ticks: int = 0

    def tick(self) -> int:
        self.ticks += 1
        return self.ticks

    def add_execution(self, status: str, lease: Optional[int] = 300) -> Dict[str, Any]:
        number = len(self.executions) + 1
        row = {"execution_id": f"exe_{number}", "attempt": number,
               "status": status, "started_at": self.tick(),
               "lease_expires_at": lease if status == "running" else None}
        self.executions.append(row)
        return row

    def newest(self) -> Optional[Dict[str, Any]]:
        return self.executions[-1] if self.executions else None

    def after(self, started_at: int) -> bool:
        return any(s["stopped_at"] > started_at for s in self.stops)

    @asynccontextmanager
    async def scoped_session(self, **_kwargs):
        yield "tx"

    async def execute(self, sql, params=(), transaction_id=None):  # noqa: C901
        latest = self.newest()
        if sql == journeys.ACTIVE_THREAD_SQL:
            return [{"active_thread_id": THREAD}]
        if sql == journeys.LATEST_EXECUTION_SQL:
            return [{"execution_id": latest["execution_id"], "status": latest["status"],
                     "already_stopped": self.after(latest["started_at"])}] if latest else []
        if sql == journeys.SNAPSHOT_SQL:
            return []
        if sql == journeys.LOCK_LATEST_EXECUTION_SQL:
            return [dict(latest)] if latest else []
        if sql == journeys.NEWER_STOP_SQL:
            return [{"already_stopped": self.after(params[1])}]
        if sql == journeys.CLOSE_LEASE_SQL:
            for row in self.executions:
                if row["execution_id"] == params[1] and row["status"] == "running":
                    row.update(status=params[0], lease_expires_at=None)
            return []
        if sql == journeys.RECORD_STOP_SQL:
            self.stops.append({"stopped_at": self.tick(), "stopped_during": params[5],
                               "released_execution_id": params[7]})
            return [{"stopped_at": "2026-11-19 12:00:00+00"}]
        raise AssertionError(f"unexpected SQL: {sql}")


class Runtime:
    def __init__(self, db: FakeAurora):
        self.db, self.stops, self.while_stopping = db, [], None

    async def stop_session(self, traveler_id, thread_id):
        self.stops.append(thread_id)
        if self.while_stopping:
            self.while_stopping()
        return SessionStop(workflow_session_id(traveler_id, thread_id), "stopped")


class Identity:
    def authorization_context(self):
        return None


@pytest.fixture
def world(monkeypatch):
    db = FakeAurora()
    runtime = Runtime(db)
    monkeypatch.setattr(journeys, "get_rds_data_client", lambda: db)
    monkeypatch.setattr(journeys, "get_workflow_runtime", lambda: runtime)
    monkeypatch.setattr(journeys, "get_agentcore_identity", lambda: Identity())
    return db, runtime


async def test_a_replacement_started_after_the_remote_stop_keeps_its_lease(world):
    db, runtime = world
    db.add_execution("paused")
    runtime.while_stopping = lambda: db.add_execution("running")

    with pytest.raises(HTTPException) as caught:
        await journeys.stop_session(JOURNEY, JORDAN, None)

    assert caught.value.status_code == 409
    new = db.newest()
    assert (new["status"], new["lease_expires_at"]) == ("running", 300)
    assert db.stops == []


async def test_the_replacement_can_itself_be_stopped_afterwards(world):
    db, runtime = world
    db.add_execution("paused")
    runtime.while_stopping = lambda: db.add_execution("running")
    with pytest.raises(HTTPException):
        await journeys.stop_session(JOURNEY, JORDAN, None)

    runtime.while_stopping = None
    reply = await journeys.stop_session(JOURNEY, JORDAN, None)

    assert reply["stopped_during"] == "running"
    assert db.newest()["status"] == "abandoned"


async def test_a_paused_journey_stops_and_records_waiting(world):
    db, _ = world
    db.add_execution("paused")
    reply = await journeys.stop_session(JOURNEY, JORDAN, None)
    assert reply["stopped"] is True and reply["stopped_during"] == "waiting"
    assert [s["released_execution_id"] for s in db.stops] == [None]
    assert db.newest()["status"] == "paused"


async def test_a_running_journey_is_released_by_its_own_stop(world):
    db, _ = world
    running = db.add_execution("running")
    reply = await journeys.stop_session(JOURNEY, JORDAN, None)
    assert reply["stopped_during"] == "running"
    assert (running["status"], running["lease_expires_at"]) == ("abandoned", None)
    assert db.stops[0]["released_execution_id"] == running["execution_id"]


async def test_an_older_running_row_with_no_lease_still_stops(world):
    db, _ = world
    older = db.add_execution("running", lease=None)
    reply = await journeys.stop_session(JOURNEY, JORDAN, None)
    assert reply["stopped_during"] == "running" and older["status"] == "abandoned"


async def test_a_repeated_stop_is_refused_before_the_runtime_is_called_again(world):
    db, runtime = world
    db.add_execution("paused")
    await journeys.stop_session(JOURNEY, JORDAN, None)
    with pytest.raises(HTTPException) as caught:
        await journeys.stop_session(JOURNEY, JORDAN, None)
    assert caught.value.status_code == 409
    assert caught.value.detail == "This session was already stopped."
    assert len(runtime.stops) == 1 and len(db.stops) == 1
