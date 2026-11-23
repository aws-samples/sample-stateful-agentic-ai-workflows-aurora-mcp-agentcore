"""A stop settles the execution it was issued against, never a replacement started after it."""

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException

from backend.agentcore.workflow_runtime import SessionStop, workflow_session_id
from backend.db.journey_store import JOURNEY_SETTLED_SQL
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
    journey_status: str = "running"
    snapshot: Optional[Dict[str, Any]] = None
    lock_error: Optional[Exception] = None

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
            return [self.snapshot] if self.snapshot else []
        if sql == journeys.LOCK_LATEST_EXECUTION_SQL:
            if self.lock_error:
                raise self.lock_error
            return [dict(latest)] if latest else []
        if sql == JOURNEY_SETTLED_SQL:
            if not any(r["status"] == "running" for r in self.executions):
                self.journey_status = params[0]
            return []
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

    reply = await journeys.stop_session(JOURNEY, JORDAN, None)

    assert reply["newer_attempt"] is True
    new = db.newest()
    assert (new["status"], new["lease_expires_at"]) == ("running", 300)
    assert len(db.stops) == 1


async def test_the_replacement_is_left_to_its_lease_and_a_second_stop_is_refused(world):
    db, runtime = world
    db.add_execution("paused")
    runtime.while_stopping = lambda: db.add_execution("running")
    await journeys.stop_session(JOURNEY, JORDAN, None)

    runtime.while_stopping = None
    with pytest.raises(HTTPException) as caught:
        await journeys.stop_session(JOURNEY, JORDAN, None)

    assert caught.value.status_code == 409
    assert db.newest()["status"] == "running"


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


def snapshot(completed, graph_status):
    return {"last_step": completed[-1], "graph_status": graph_status,
            "completed_nodes": json.dumps(completed)}


def lock_timeout():
    return ClientError(
        {"Error": {"Code": "BadRequestException", "Message":
                   "ERROR: canceling statement due to lock timeout\n  Where: while locking "
                   'tuple (0,3) in relation "journey_executions"; SQLState: 55P03'}},
        "ExecuteStatement")


@pytest.mark.parametrize("completed, graph_status, expected", [
    (["classify", "synthesize"], "completed", True),
    (["classify", "hold", "synthesize"], "failed", True),
    (["classify", "search"], "executing", False),
    (["classify", "hold"], "interrupted", False),
])
def test_run_finished_means_synthesize_completed(completed, graph_status, expected):
    assert journeys.run_finished(snapshot(completed, graph_status)) is expected


def test_run_finished_is_false_without_a_snapshot_or_a_node_list():
    assert journeys.run_finished(None) is False
    assert journeys.run_finished({"last_step": None, "graph_status": None,
                                  "completed_nodes": None}) is False


async def test_a_retried_hold_run_that_failed_after_synthesize_is_recorded_finished(world):
    db, runtime = world
    db.add_execution("running")
    db.snapshot = snapshot(["classify", "hold", "synthesize"], "failed")
    runtime.while_stopping = lambda: db.executions[0].update(status="failed")
    reply = await journeys.stop_session(JOURNEY, JORDAN, None)
    assert reply["stopped_during"] == "finished"
    assert db.stops[0]["released_execution_id"] is None


async def test_a_stop_settles_the_journey_with_the_execution_outcome(world):
    db, _ = world
    db.add_execution("running")
    await journeys.stop_session(JOURNEY, JORDAN, None)
    assert db.journey_status == "abandoned"


async def test_a_stop_of_a_finished_running_execution_settles_the_journey_succeeded(world):
    db, _ = world
    db.add_execution("running")
    db.snapshot = snapshot(["classify", "synthesize"], "completed")
    await journeys.stop_session(JOURNEY, JORDAN, None)
    assert db.journey_status == "succeeded"


async def test_a_paused_stop_leaves_the_journey_status_alone(world):
    db, _ = world
    db.add_execution("paused")
    db.journey_status = "paused"
    await journeys.stop_session(JOURNEY, JORDAN, None)
    assert db.journey_status == "paused"


async def test_a_lock_timeout_on_the_settle_is_409_with_a_retry_message(world):
    db, _ = world
    db.add_execution("running")
    db.lock_error = lock_timeout()
    with pytest.raises(HTTPException) as caught:
        await journeys.stop_session(JOURNEY, JORDAN, None)
    assert caught.value.status_code == 409
    assert "try again" in caught.value.detail.lower()
    assert db.stops == []


async def test_another_database_error_on_the_settle_is_not_hidden(world):
    db, _ = world
    db.add_execution("running")
    db.lock_error = ClientError(
        {"Error": {"Code": "BadRequestException", "Message": "ERROR: syntax error"}},
        "ExecuteStatement")
    with pytest.raises(ClientError):
        await journeys.stop_session(JOURNEY, JORDAN, None)


async def test_a_replacement_added_before_the_stop_lands_still_records_the_stop(world):
    db, runtime = world
    original = db.add_execution("paused")
    runtime.while_stopping = lambda: db.add_execution("running")

    reply = await journeys.stop_session(JOURNEY, JORDAN, None)

    assert reply["stopped"] is True and reply["newer_attempt"] is True
    assert "newer attempt" in reply["notice"]
    assert reply["stopped_during"] == "waiting"
    assert [s["released_execution_id"] for s in db.stops] == [None]
    assert (original["status"], db.newest()["status"]) == ("paused", "running")
    assert db.newest()["lease_expires_at"] == 300
    assert db.journey_status == "running"


async def test_a_stop_without_a_replacement_says_nothing_of_a_newer_attempt(world):
    db, _ = world
    db.add_execution("paused")
    reply = await journeys.stop_session(JOURNEY, JORDAN, None)
    assert "newer_attempt" not in reply and "notice" not in reply
