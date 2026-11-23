"""Live Aurora probe backing /api/health: a real query, cached briefly.

The old `/api/health` hardcoded `status: "healthy"` and read only startup-time
snapshot state, so it kept reporting healthy while the live backend's AWS
credentials had actually expired and every real Aurora read was failing. This
probe is what makes the reported status track reality instead.
"""

import asyncio
import time

import pytest

from backend import health_probe


@pytest.fixture(autouse=True)
def _clean_probe_cache():
    health_probe.reset_probe_cache()
    yield
    health_probe.reset_probe_cache()


class _OkDb:
    def __init__(self):
        self.calls = 0

    async def execute_one(self, sql, params=None):
        self.calls += 1
        return {"snapshots": True, "stops": True}


class _FailingDb:
    def __init__(self, exc):
        self.calls = 0
        self.exc = exc

    async def execute_one(self, sql, params=None):
        self.calls += 1
        raise self.exc


class _SlowDb:
    async def execute_one(self, sql, params=None):
        await asyncio.sleep(10)
        return {"snapshots": True, "stops": True}


def test_probe_passes_when_aurora_answers_and_the_snapshot_table_exists(monkeypatch):
    db = _OkDb()
    monkeypatch.setattr(health_probe, "get_rds_data_client", lambda: db)

    result = asyncio.run(health_probe.probe_aurora())

    assert result.ok is True
    assert result.error_class is None
    assert db.calls == 1


class _TableDb:
    def __init__(self, snapshots, stops=True):
        self.snapshots, self.stops, self.sql = snapshots, stops, []

    async def execute_one(self, sql, params=None):
        self.sql.append(sql)
        return {"snapshots": self.snapshots, "stops": self.stops}


def test_probe_checks_that_the_workflow_snapshot_table_exists(monkeypatch):
    db = _TableDb(True)
    monkeypatch.setattr(health_probe, "get_rds_data_client", lambda: db)

    result = asyncio.run(health_probe.probe_aurora())

    assert result.ok is True
    assert result.component is None
    assert "to_regclass('public.workflow_snapshots')" in db.sql[0]


def test_a_reachable_cluster_without_the_snapshot_table_is_degraded(monkeypatch):
    monkeypatch.setattr(health_probe, "get_rds_data_client", lambda: _TableDb(False))

    result = asyncio.run(health_probe.probe_aurora())

    assert result.ok is False
    assert result.component == "workflow_snapshots"
    assert result.error_class is None


def test_probe_checks_that_the_session_stops_table_exists(monkeypatch):
    db = _TableDb(True)
    monkeypatch.setattr(health_probe, "get_rds_data_client", lambda: db)

    asyncio.run(health_probe.probe_aurora())

    assert "to_regclass('public.workflow_session_stops')" in db.sql[0]


def test_a_reachable_cluster_without_the_session_stops_table_is_degraded(monkeypatch):
    monkeypatch.setattr(
        health_probe, "get_rds_data_client", lambda: _TableDb(True, stops=False)
    )

    result = asyncio.run(health_probe.probe_aurora())

    assert result.ok is False
    assert result.component == "workflow_session_stops"
    assert result.error_class is None


def test_a_missing_snapshot_table_is_named_before_a_missing_stops_table(monkeypatch):
    monkeypatch.setattr(
        health_probe, "get_rds_data_client", lambda: _TableDb(False, stops=False)
    )

    result = asyncio.run(health_probe.probe_aurora())

    assert result.component == "workflow_snapshots"


def test_an_unreachable_cluster_names_aurora_as_the_component(monkeypatch):
    monkeypatch.setattr(
        health_probe, "get_rds_data_client", lambda: _FailingDb(RuntimeError("down"))
    )

    result = asyncio.run(health_probe.probe_aurora())

    assert result.component == "aurora"


def test_probe_fails_closed_with_error_class_not_error_text(monkeypatch):
    secret_leak = "arn:aws:secretsmanager:us-east-1:123456789012:secret:prod/aurora-abc123"
    db = _FailingDb(RuntimeError(f"ExpiredTokenException: token expired ({secret_leak})"))
    monkeypatch.setattr(health_probe, "get_rds_data_client", lambda: db)

    result = asyncio.run(health_probe.probe_aurora())

    assert result.ok is False
    assert result.error_class == "RuntimeError"
    # The error class is reported; the exception's message text - which may
    # carry internal detail like an ARN - must never leak into the result.
    assert secret_leak not in repr(result)
    assert "token expired" not in repr(result)


def test_probe_times_out_instead_of_hanging(monkeypatch):
    monkeypatch.setattr(health_probe, "get_rds_data_client", lambda: _SlowDb())
    monkeypatch.setattr(health_probe, "PROBE_TIMEOUT_SECONDS", 0.05)

    result = asyncio.run(health_probe.probe_aurora())

    assert result.ok is False
    assert result.error_class == "TimeoutError"


def test_probe_result_is_cached_for_up_to_ten_seconds(monkeypatch):
    db = _OkDb()
    monkeypatch.setattr(health_probe, "get_rds_data_client", lambda: db)
    clock = {"now": 1000.0}
    monkeypatch.setattr(health_probe.time, "monotonic", lambda: clock["now"])

    first = asyncio.run(health_probe.probe_aurora())
    assert db.calls == 1

    # A failing db is swapped in, but the cache is still fresh - the cached
    # "ok" result must win without a second real probe.
    monkeypatch.setattr(health_probe, "get_rds_data_client", lambda: _FailingDb(RuntimeError("boom")))
    clock["now"] += 5.0
    second = asyncio.run(health_probe.probe_aurora())
    assert second.ok is True
    assert db.calls == 1
    assert second is first


def test_probe_cache_expires_after_ten_seconds(monkeypatch):
    db = _OkDb()
    monkeypatch.setattr(health_probe, "get_rds_data_client", lambda: db)
    clock = {"now": 1000.0}
    monkeypatch.setattr(health_probe.time, "monotonic", lambda: clock["now"])

    asyncio.run(health_probe.probe_aurora())
    assert db.calls == 1

    failing = _FailingDb(RuntimeError("boom"))
    monkeypatch.setattr(health_probe, "get_rds_data_client", lambda: failing)
    clock["now"] += 10.001
    result = asyncio.run(health_probe.probe_aurora())

    assert result.ok is False
    assert failing.calls == 1


class _LoginDb:
    async def execute_one(self, sql, params=None):
        assert "current_user" in sql
        return {"snapshots": True, "stops": True, "db_user": "meridian_backend"}


def test_the_probe_reports_the_login_the_query_ran_as(monkeypatch):
    monkeypatch.setattr(health_probe, "get_rds_data_client", lambda: _LoginDb())

    result = asyncio.run(health_probe.probe_aurora())

    assert result.ok is True and result.db_user == "meridian_backend"


class _BlockingDb:
    """A Data API call that cannot be cancelled: a thread that sleeps, as the real one does."""

    def __init__(self, seconds):
        self.calls = 0
        self.seconds = seconds

    async def execute_one(self, sql, params=None):
        from backend.db.rds_data_client import run_blocking_shielded

        self.calls += 1
        await run_blocking_shielded(time.sleep, self.seconds)
        return {"snapshots": True, "stops": True, "db_user": "meridian_backend"}


def test_a_call_the_shield_keeps_awaiting_still_answers_within_the_bound(monkeypatch):
    db = _BlockingDb(1.0)
    monkeypatch.setattr(health_probe, "get_rds_data_client", lambda: db)
    monkeypatch.setattr(health_probe, "PROBE_TIMEOUT_SECONDS", 0.1)

    async def scenario():
        started = time.monotonic()
        result = await health_probe.probe_aurora()
        return result, time.monotonic() - started

    result, elapsed = asyncio.run(scenario())

    assert elapsed < 0.5
    assert result.ok is False
    assert result.component == "aurora"
    assert result.error_class == "TimeoutError"


def test_only_one_probe_is_in_flight_and_a_second_caller_gets_the_stale_result(monkeypatch):
    db = _BlockingDb(0.6)
    monkeypatch.setattr(health_probe, "get_rds_data_client", lambda: db)
    monkeypatch.setattr(health_probe, "PROBE_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(health_probe, "CACHE_TTL_SECONDS", 0.0)
    health_probe._cache = health_probe.AuroraProbeResult(ok=True, db_user="meridian_backend")
    health_probe._cache_at = time.monotonic()

    async def scenario():
        started = time.monotonic()
        first = await health_probe.probe_aurora()
        second = await health_probe.probe_aurora()
        return first, second, time.monotonic() - started

    first, second, elapsed = asyncio.run(scenario())

    assert db.calls == 1
    assert elapsed < 0.5
    assert first.stale is True and first.ok is True
    assert second.stale is True and second.ok is True


def test_a_probe_that_finishes_late_becomes_the_next_answer(monkeypatch):
    db = _BlockingDb(0.3)
    monkeypatch.setattr(health_probe, "get_rds_data_client", lambda: db)
    monkeypatch.setattr(health_probe, "PROBE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(health_probe, "CACHE_TTL_SECONDS", 60.0)

    async def scenario():
        first = await health_probe.probe_aurora()
        await asyncio.sleep(0.5)
        return first, await health_probe.probe_aurora()

    first, second = asyncio.run(scenario())

    assert first.ok is False
    assert second.ok is True and second.stale is False
    assert db.calls == 1


def test_a_stale_result_older_than_the_limit_is_unknown_not_trusted(monkeypatch):
    db = _BlockingDb(0.5)
    monkeypatch.setattr(health_probe, "get_rds_data_client", lambda: db)
    monkeypatch.setattr(health_probe, "PROBE_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(health_probe, "CACHE_TTL_SECONDS", 0.0)
    health_probe._cache = health_probe.AuroraProbeResult(ok=True)
    health_probe._cache_at = time.monotonic() - health_probe.STALE_LIMIT_SECONDS - 1

    result = asyncio.run(health_probe.probe_aurora())

    assert result.ok is False
    assert result.error_class == "TimeoutError"


def test_api_health_answers_within_the_bound_while_aurora_hangs(monkeypatch):
    from backend import main

    monkeypatch.setattr(health_probe, "get_rds_data_client", lambda: _BlockingDb(1.0))
    monkeypatch.setattr(health_probe, "PROBE_TIMEOUT_SECONDS", 0.1)

    async def scenario():
        started = time.monotonic()
        payload = await main._health_payload()
        return payload, time.monotonic() - started

    payload, elapsed = asyncio.run(scenario())

    assert elapsed < 0.5
    assert payload.status == "degraded"
    assert payload.aurora_reachable is False
