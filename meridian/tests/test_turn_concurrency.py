"""Long Runtime turns cannot starve sign-in or Aurora calls, and too many turns get a 429."""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import backend.routers.chat as chat_router
from backend import http_auth, main, turn_capacity
from backend.agentcore.caller_credential import bind_caller_token, current_caller_token
from backend.agents.phase_04_production import production_agent as production_agent_mod
from backend.db.rds_data_client import run_blocking_shielded
from backend.http_auth import HttpPrincipal, require_http_principal
from tests.test_production_transaction_boundaries import build_agent, runtime_decision

PRINCIPAL = HttpPrincipal("sub-jordan", "trv_meridian_demo", "cognito")
PROMPT_WAIT_SECONDS = 2.0


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(main.app.dependency_overrides, require_http_principal, lambda: PRINCIPAL)
    return TestClient(main.app, raise_server_exceptions=False)


async def fill_the_runtime_pool(release: threading.Event) -> list:
    """Occupy every worker of the Runtime pool with a call that waits for ``release``."""
    started = [threading.Event() for _ in range(turn_capacity.RUNTIME_TURN_WORKERS)]
    calls = [
        asyncio.create_task(turn_capacity.run_runtime_call(
            lambda ready=ready: (ready.set(), release.wait(30))))
        for ready in started
    ]
    while not all(ready.is_set() for ready in started):
        await asyncio.sleep(0.01)
    return calls


def test_the_cap_is_not_below_the_pool_so_legitimate_turns_are_never_refused():
    assert turn_capacity.MAX_CONCURRENT_TURNS >= turn_capacity.RUNTIME_TURN_WORKERS
    assert turn_capacity.RUNTIME_TURN_WORKERS < 32  # the default pool is min(32, cpus + 4)


def test_sign_in_and_aurora_calls_finish_while_every_runtime_worker_is_busy():
    verifier = SimpleNamespace(verify=lambda token: SimpleNamespace(
        subject_id="sub-jordan", traveler_id="trv_meridian_demo"))

    async def scenario():
        release = threading.Event()
        calls = await fill_the_runtime_pool(release)
        try:
            started = time.monotonic()
            principal = await asyncio.wait_for(
                http_auth._cognito_principal(verifier, "token"), PROMPT_WAIT_SECONDS)
            rows = await asyncio.wait_for(
                run_blocking_shielded(lambda: "aurora-rows"), PROMPT_WAIT_SECONDS)
            return principal, rows, time.monotonic() - started
        finally:
            release.set()
            await asyncio.gather(*calls)

    principal, rows, elapsed = asyncio.run(scenario())
    assert principal.traveler_id == "trv_meridian_demo"
    assert rows == "aurora-rows"
    assert elapsed < PROMPT_WAIT_SECONDS


def test_the_runtime_call_runs_on_the_runtime_pool_with_the_callers_context(monkeypatch):
    seen = {}

    def invoke_turn(*args, **kwargs):
        seen["thread"] = threading.current_thread().name
        seen["token"] = current_caller_token()
        return runtime_decision()

    agent = build_agent([], runtime_decision(), [])
    agent.agentcore_runtime = SimpleNamespace(invoke_turn=invoke_turn)
    monkeypatch.setattr(production_agent_mod, "require_agentcore_platform", lambda **_kw: None)

    async def scenario():
        bind_caller_token("caller-token")
        return await agent.process_turn("Tokyo", "trv_meridian_demo", None, 4)

    asyncio.run(scenario())
    assert seen["thread"].startswith(turn_capacity.RUNTIME_THREAD_PREFIX)
    assert seen["token"] == "caller-token"


def test_a_turn_beyond_the_cap_is_refused_with_a_429():
    async def scenario():
        held = []
        for _ in range(turn_capacity.MAX_CONCURRENT_TURNS):
            slot = turn_capacity.turn_slot()
            await slot.__aenter__()
            held.append(slot)
        try:
            with pytest.raises(HTTPException) as refused:
                async with turn_capacity.turn_slot():
                    pytest.fail("a turn beyond the cap must not start")
            return refused.value
        finally:
            for slot in held:
                await slot.__aexit__(None, None, None)

    refused = asyncio.run(scenario())
    assert refused.status_code == 429
    assert refused.headers["Retry-After"]
    assert turn_capacity._in_flight == 0


@pytest.mark.parametrize("route, body", [
    ("/api/chat/order", {"product_id": "CTY-002", "phase": 4, "conversation_id": "c1"}),
    ("/api/chat/order/stream", {"product_id": "CTY-002", "phase": 4, "conversation_id": "c1"}),
    ("/api/chat/book", {"booking_id": "HLD-1", "conversation_id": "c1"}),
    ("/api/chat/book/stream", {"booking_id": "HLD-1", "conversation_id": "c1"}),
    ("/api/chat", {"message": "Tokyo", "phase": 4}),
    ("/api/chat/stream", {"message": "Tokyo", "phase": 4}),
])
def test_every_turn_route_answers_a_429_json_error_at_the_cap(monkeypatch, client, route, body):
    for name in ("production_hold", "production_booking", "production_search"):
        monkeypatch.setattr(chat_router, name, AsyncMock(side_effect=AssertionError("ran")))
    monkeypatch.setattr(turn_capacity, "_in_flight", turn_capacity.MAX_CONCURRENT_TURNS)

    response = client.post(route, json=body)

    assert response.status_code == 429
    assert response.json()["error"]
    assert response.json()["code"] == turn_capacity.TURNS_BUSY_CODE
    assert response.headers["retry-after"]


def test_a_slot_is_given_back_when_the_turn_ends_or_fails():
    async def scenario():
        for _ in range(turn_capacity.MAX_CONCURRENT_TURNS * 2):
            with pytest.raises(RuntimeError):
                async with turn_capacity.turn_slot():
                    raise RuntimeError("turn failed")
        async with turn_capacity.turn_slot():
            return turn_capacity._in_flight

    assert asyncio.run(scenario()) == 1
    assert turn_capacity._in_flight == 0


def test_a_phase_that_never_calls_a_model_takes_no_slot(monkeypatch, client):
    monkeypatch.setattr(turn_capacity, "_in_flight", turn_capacity.MAX_CONCURRENT_TURNS)
    search = AsyncMock(return_value=([], []))
    monkeypatch.setattr(chat_router, "sql_search", search)

    response = client.post("/api/chat", json={"message": "Tokyo", "phase": 1})

    assert response.status_code != 429
    search.assert_awaited_once()
