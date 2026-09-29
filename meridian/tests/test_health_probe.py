"""Live Aurora probe backing /api/health: real SELECT 1, cached briefly.

The old `/api/health` hardcoded `status: "healthy"` and read only startup-time
checkpoint state, so it kept reporting healthy while the live backend's AWS
credentials had actually expired and every real Aurora read was failing. This
probe is what makes the reported status track reality instead.
"""

import asyncio

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
        return {"?column?": 1}


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
        return {"?column?": 1}


def test_probe_passes_when_aurora_answers_select_1(monkeypatch):
    db = _OkDb()
    monkeypatch.setattr(health_probe, "get_rds_data_client", lambda: db)

    result = asyncio.run(health_probe.probe_aurora())

    assert result.ok is True
    assert result.error_class is None
    assert db.calls == 1


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
