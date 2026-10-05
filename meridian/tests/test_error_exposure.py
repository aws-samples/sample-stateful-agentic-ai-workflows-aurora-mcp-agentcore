"""Raw exception text stays in the server log; clients get a fixed message and a reference."""

import logging
import re
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from backend.http_auth import HttpPrincipal
from backend.logging_config import log_exception
from backend.routers import chat as router
from backend.routers import diagnostics

SECRET = "arn:aws:rds:us-east-1:123456789012:cluster:secret-cluster password=hunter2"
PRINCIPAL = HttpPrincipal("test", "alice", "test")
REFERENCE = re.compile(r"Reference ([0-9a-f]{8})")


def _client_text(response) -> str:
    parts = [response.message] + [a.details or "" for a in response.activities]
    return "\n".join(parts)


def _assert_logged_with_traceback(caplog, ref: str) -> None:
    records = [r for r in caplog.records if ref in r.getMessage() and r.exc_info]
    assert records, "the reference must tie to a log record carrying the traceback"
    assert SECRET in str(records[0].exc_info[1])


def test_log_exception_returns_a_reference_and_logs_the_traceback(caplog):
    with caplog.at_level(logging.ERROR):
        try:
            raise RuntimeError(SECRET)
        except RuntimeError:
            ref = log_exception("unit_test")
    assert re.fullmatch(r"[0-9a-f]{8}", ref)
    _assert_logged_with_traceback(caplog, ref)


@pytest.mark.asyncio
async def test_a_failing_search_does_not_leak_the_exception_text(monkeypatch, caplog):
    monkeypatch.setattr(router, "sql_search", AsyncMock(side_effect=RuntimeError(SECRET)))
    with caplog.at_level(logging.ERROR):
        response = await router.chat(router.ChatRequest(message="Tokyo", phase=1), PRINCIPAL)
    text = _client_text(response)
    assert "hunter2" not in text and "secret-cluster" not in text
    ref = REFERENCE.search(text).group(1)
    _assert_logged_with_traceback(caplog, ref)


@pytest.mark.asyncio
async def test_a_failing_production_turn_does_not_leak_the_exception_text(monkeypatch, caplog):
    monkeypatch.setattr(router, "production_search", AsyncMock(side_effect=RuntimeError(SECRET)))
    with caplog.at_level(logging.ERROR):
        response = await router.chat(router.ChatRequest(message="Tokyo", phase=4), PRINCIPAL)
    text = _client_text(response)
    assert "hunter2" not in text
    ref = REFERENCE.search(text).group(1)
    _assert_logged_with_traceback(caplog, ref)
    assert any(a.activity_type == "error" for a in response.activities)


@pytest.mark.asyncio
async def test_a_failing_package_agent_does_not_leak_the_exception_text(monkeypatch, caplog):
    monkeypatch.setattr(
        router, "retrieval_availability_search", AsyncMock(side_effect=RuntimeError(SECRET))
    )
    monkeypatch.setattr(router, "retrieval_supervisor_search", AsyncMock(return_value=([], [])))
    request = router.ChatRequest(message="Is TKY-003 available in June?", phase=3)
    with caplog.at_level(logging.ERROR):
        response = await router.chat(request, PRINCIPAL)
    text = _client_text(response)
    assert "hunter2" not in text
    assert "availability lookup failed" in text
    _assert_logged_with_traceback(caplog, REFERENCE.search(text).group(1))


@pytest.mark.asyncio
async def test_a_failing_concierge_tool_does_not_leak_the_exception_text(monkeypatch, caplog):
    monkeypatch.setattr(router, "_call_domain_tool", AsyncMock(side_effect=RuntimeError(SECRET)))
    activities: list = []
    with caplog.at_level(logging.ERROR):
        _, domain_text, _ = await router._concierge_mcp_turn("compare", "alice", [], activities)
    text = domain_text + "\n".join(a.details or "" for a in activities)
    assert "hunter2" not in text
    _assert_logged_with_traceback(caplog, REFERENCE.search(text).group(1))


@pytest.mark.asyncio
async def test_rls_probe_errors_do_not_leak_the_exception_text(monkeypatch, caplog):
    @asynccontextmanager
    async def scoped_session(**kwargs):
        raise RuntimeError(SECRET)
        yield

    decision = SimpleNamespace(
        allowed=True, provider="p", subject_id="s", principal="x", traveler_id="alice",
        decision="allow", binding_id="b", audit_id="a", reason=None,
    )
    db = Mock(scoped_session=scoped_session,
              check_traveler_authorization=AsyncMock(return_value=decision),
              execute=AsyncMock(return_value=[]))
    monkeypatch.setattr(diagnostics, "get_rds_data_client", lambda: db)
    monkeypatch.setattr(diagnostics, "get_agentcore_identity", lambda: Mock())
    with caplog.at_level(logging.ERROR):
        response = await diagnostics.rls_probe(
            diagnostics.RlsProbeRequest(traveler_id="alice"), PRINCIPAL
        )
    dumped = response.model_dump_json()
    assert "hunter2" not in dumped
    assert response.debug is not None and REFERENCE.search(response.debug["error"])
    assert all(
        REFERENCE.search(t.error) for t in response.tables if t.error
    ), "every failed table reports a reference"
    _assert_logged_with_traceback(caplog, REFERENCE.search(response.debug["error"]).group(1))
