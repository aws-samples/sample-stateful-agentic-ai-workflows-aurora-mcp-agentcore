"""An expired caller token reaches the browser as a retryable 401, not a 503 or a chat error."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend import main
from backend.agentcore.errors import CallerTokenExpired, CallerTokenMissing
from backend.agents.phase_04_production import concierge as concierge_mod
from backend.http_auth import HttpPrincipal, require_http_principal
from backend.main import http_exception_handler
from backend.routers import chat as chat_mod
from backend.routers import journeys
from backend.routers.chat import BookingRequest, OrderRequest
from backend.token_expiry import CHALLENGE, is_token_expired, token_expired_error

GET_RUNTIME = "backend.agentcore.workflow_runtime.get_workflow_runtime"


def principal():
    return HttpPrincipal("test", "trv_meridian_demo", "test")


def assert_retryable(error: HTTPException):
    assert error.status_code == 401
    assert error.headers == {"WWW-Authenticate": CHALLENGE}
    assert is_token_expired(error)


def test_the_error_is_a_bearer_challenge_with_a_stable_code():
    error = token_expired_error()
    assert_retryable(error)
    assert "sign-in expired" in error.detail and "token" not in error.detail.lower()
    assert not is_token_expired(HTTPException(401, "nope", headers={"WWW-Authenticate": "Bearer"}))
    assert not is_token_expired(HTTPException(403, "nope"))


def test_the_apps_error_handler_adds_the_code_and_keeps_the_challenge():
    local = FastAPI()
    local.add_exception_handler(HTTPException, http_exception_handler)

    @local.get("/expired")
    async def expired():
        raise token_expired_error()

    @local.get("/forbidden")
    async def forbidden():
        raise HTTPException(status_code=403, detail="no")

    client = TestClient(local)
    response = client.get("/expired")
    assert response.status_code == 401
    assert response.json() == {"error": token_expired_error().detail, "code": "token_expired"}
    assert response.headers["www-authenticate"] == CHALLENGE
    assert client.get("/forbidden").json() == {"error": "no"}


async def test_a_workflow_turn_stopped_by_an_expired_token_is_a_retryable_401(monkeypatch):
    class Runtime:
        async def run(self, command):
            raise CallerTokenExpired("expired")

    monkeypatch.setattr(GET_RUNTIME, lambda: Runtime())
    with pytest.raises(HTTPException) as caught:
        await chat_mod.chat(chat_mod.ChatRequest(message="Tokyo recovery", phase=5), principal())
    assert_retryable(caught.value)


async def test_a_concierge_turn_stopped_by_an_expired_token_is_a_retryable_401(monkeypatch):
    async def expired(*args, **kwargs):
        raise CallerTokenExpired("expired")

    monkeypatch.setattr(chat_mod, "production_search", expired)
    with pytest.raises(HTTPException) as caught:
        await chat_mod.chat(chat_mod.ChatRequest(message="Quiet beach week", phase=4), principal())
    assert_retryable(caught.value)


def agent_raising(method):
    async def raise_expired(*args, **kwargs):
        raise CallerTokenExpired("expired")

    return lambda: SimpleNamespace(**{method: raise_expired})


def test_a_hold_stopped_by_an_expired_token_is_a_retryable_401(monkeypatch):
    async def package(product_id):
        return {"available_sizes": ["7 nights"]}, {"product_id": "CTY-002", "price": 1000.0}

    monkeypatch.setattr(chat_mod, "_package_for_hold", package)
    monkeypatch.setattr(chat_mod, "_requested_duration", lambda row, size: "7 nights")
    monkeypatch.setattr(concierge_mod, "create_production_agent", agent_raising("process_hold"))
    request = OrderRequest(product_id="CTY-002", quantity=2, size="7 nights", phase=4)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(chat_mod.production_hold(request))
    assert_retryable(caught.value)


def test_a_booking_stopped_by_an_expired_token_is_a_retryable_401(monkeypatch):
    line = {"booking_id": "HLD-1", "status": "held", "total_amount": "4998.00",
            "package_id": "CTY-002", "duration": "5 nights", "travelers_count": 2,
            "unit_price": "2499.00"}

    async def traveler_booking(traveler_id, booking_id):
        return dict(line)

    async def package(product_id):
        return {}, {"product_id": "CTY-002", "name": "Tokyo", "price": 2499.0}

    monkeypatch.setattr(chat_mod, "_traveler_booking", traveler_booking)
    monkeypatch.setattr(chat_mod, "_package_for_hold", package)
    monkeypatch.setattr(concierge_mod, "create_production_agent", agent_raising("process_booking"))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(chat_mod.production_booking(BookingRequest(booking_id="HLD-1")))
    assert_retryable(caught.value)


async def test_the_stream_reports_the_code_on_its_error_event(monkeypatch):
    async def expired(request, who):
        raise token_expired_error()

    monkeypatch.setattr(chat_mod, "chat", expired)
    response = await chat_mod.stream_chat(
        chat_mod.ChatRequest(message="Quiet beach week", phase=4), principal())
    events = []
    async for frame in response.body_iterator:
        if frame.startswith("data: "):
            events.append(json.loads(frame[6:]))
    error = next(e for e in events if e["type"] == "error")
    assert error["code"] == "token_expired" and "sign-in expired" in error["message"]

# ---- Route level: the same 401 body through the real app, for every route family ----


EXPIRED_BODY = {"error": token_expired_error().detail, "code": "token_expired"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(main.app.dependency_overrides, require_http_principal, principal)
    return TestClient(main.app, raise_server_exceptions=False)


def assert_expired_response(response):
    assert response.status_code == 401
    assert response.json() == EXPIRED_BODY
    assert response.headers["www-authenticate"] == CHALLENGE
    assert "outage" not in response.text.lower()


def test_the_chat_route_answers_the_expired_body(client, monkeypatch):
    async def expired(*args, **kwargs):
        raise CallerTokenExpired("secret-detail")

    monkeypatch.setattr(chat_mod, "production_search", expired)
    response = client.post("/api/chat", json={"message": "Quiet beach week", "phase": 4})
    assert_expired_response(response)
    assert "secret-detail" not in response.text


def test_the_workflow_route_answers_the_expired_body(client, monkeypatch):
    class Runtime:
        async def run(self, command):
            raise CallerTokenExpired("secret-detail")

    monkeypatch.setattr(GET_RUNTIME, lambda: Runtime())
    response = client.post("/api/chat", json={"message": "Tokyo recovery", "phase": 5})
    assert_expired_response(response)


def test_the_order_route_answers_the_expired_body(client, monkeypatch):
    async def package(product_id):
        return {"available_sizes": ["7 nights"]}, {"product_id": "CTY-002", "price": 1000.0}

    monkeypatch.setattr(chat_mod, "_package_for_hold", package)
    monkeypatch.setattr(chat_mod, "_requested_duration", lambda row, size: "7 nights")
    monkeypatch.setattr(concierge_mod, "create_production_agent", agent_raising("process_hold"))
    response = client.post("/api/chat/order", json={
        "product_id": "CTY-002", "quantity": 2, "size": "7 nights", "phase": 4})
    assert_expired_response(response)


def test_the_book_route_answers_the_expired_body(client, monkeypatch):
    async def traveler_booking(traveler_id, booking_id):
        return {"booking_id": "HLD-1", "package_id": "CTY-002", "duration": "5 nights",
                "total_amount": "4998.00", "travelers_count": 2}

    async def package(product_id):
        return {}, {"product_id": "CTY-002", "name": "Tokyo", "price": 2499.0}

    monkeypatch.setattr(chat_mod, "_traveler_booking", traveler_booking)
    monkeypatch.setattr(chat_mod, "_package_for_hold", package)
    monkeypatch.setattr(concierge_mod, "create_production_agent", agent_raising("process_booking"))
    response = client.post("/api/chat/book", json={"booking_id": "HLD-1"})
    assert_expired_response(response)


def stop_route(client, monkeypatch, error):
    class Runtime:
        async def stop_session(self, traveler_id, thread_id):
            raise error("secret-detail")

    async def stop_target(db, journey_id, owner):
        return "thread-1"

    monkeypatch.setattr(journeys, "_stop_target", stop_target)
    monkeypatch.setattr(journeys, "get_rds_data_client", lambda: object())
    monkeypatch.setattr(journeys, "get_workflow_runtime", lambda: Runtime())
    return client.post("/api/journeys/jrn_x/stop-session")


def test_the_journeys_route_answers_the_expired_body(client, monkeypatch):
    response = stop_route(client, monkeypatch, CallerTokenExpired)
    assert_expired_response(response)
    assert "secret-detail" not in response.text


def test_a_missing_token_is_a_401_without_the_expired_code(client, monkeypatch):
    response = stop_route(client, monkeypatch, CallerTokenMissing)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "code" not in response.json()
    assert "secret-detail" not in response.text


def sse_events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines()
            if line.startswith("data: ")]


def test_the_stream_route_ends_with_the_coded_error_event(client, monkeypatch):
    async def expired(*args, **kwargs):
        raise CallerTokenExpired("secret-detail")

    monkeypatch.setattr(chat_mod, "production_search", expired)
    response = client.post("/api/chat/stream", json={"message": "Quiet beach week", "phase": 4})
    events = sse_events(response)
    assert events[-1] == {"type": "error", "message": token_expired_error().detail,
                          "code": "token_expired"}
    assert "secret-detail" not in response.text


async def test_a_token_error_raised_inside_the_stream_still_carries_the_code(monkeypatch):
    async def expired(request, who):
        raise CallerTokenExpired("secret-detail")

    monkeypatch.setattr(chat_mod, "chat", expired)
    response = await chat_mod.stream_chat(
        chat_mod.ChatRequest(message="Quiet beach week", phase=4), principal())
    events = [json.loads(frame[6:]) async for frame in response.body_iterator
              if frame.startswith("data: ")]
    error = next(e for e in events if e["type"] == "error")
    assert error["code"] == "token_expired" and "secret-detail" not in json.dumps(error)

