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
from backend.token_expiry import (
    CHALLENGE,
    MESSAGE,
    SIGN_IN_MESSAGE,
    credential_error,
    is_token_expired,
    token_expired_error,
)

GET_RUNTIME = "backend.agentcore.workflow_runtime.get_workflow_runtime"

# (raised error, code in the body, message, WWW-Authenticate header)
CASES = [
    pytest.param(CallerTokenExpired, "token_expired", MESSAGE, CHALLENGE, id="expired"),
    pytest.param(CallerTokenMissing, "sign_in_required", SIGN_IN_MESSAGE, "Bearer", id="missing"),
]
FORBIDDEN_WORDS = ("outage", "interrupted", "error in production mode", "secret-detail")


def principal():
    return HttpPrincipal("test", "trv_meridian_demo", "test")


def assert_retryable(error: HTTPException):
    assert error.status_code == 401
    assert error.headers == {"WWW-Authenticate": CHALLENGE}
    assert is_token_expired(error)


def test_the_error_is_a_bearer_challenge_with_a_stable_code():
    error = token_expired_error()
    assert_retryable(error)
    assert error.detail == MESSAGE and "token" not in error.detail.lower()
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
    assert error["code"] == "token_expired" and error["message"] == MESSAGE


# ---- Route level: the same 401 body through the real app, for every route family ----


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setitem(main.app.dependency_overrides, require_http_principal, principal)
    return TestClient(main.app, raise_server_exceptions=False)


def assert_sign_in_response(response, code, message, challenge):
    assert response.status_code == 401
    assert response.json() == {"error": message, "code": code}
    assert response.headers["www-authenticate"] == challenge
    lowered = response.text.lower()
    assert not any(word in lowered for word in FORBIDDEN_WORDS)
    assert (code == "token_expired") == ("token_expired" in lowered)


def raising(error):
    async def raise_error(*args, **kwargs):
        raise error("secret-detail")

    return raise_error


def agent_failing(method, error):
    return lambda: SimpleNamespace(**{method: raising(error)})


@pytest.mark.parametrize("error, code, message, challenge", CASES)
def test_the_chat_route_answers_the_sign_in_body(client, monkeypatch, error, code, message,
                                                 challenge):
    monkeypatch.setattr(chat_mod, "production_search", raising(error))
    response = client.post("/api/chat", json={"message": "Quiet beach week", "phase": 4})
    assert_sign_in_response(response, code, message, challenge)


@pytest.mark.parametrize("error, code, message, challenge", CASES)
def test_the_workflow_route_answers_the_sign_in_body(client, monkeypatch, error, code, message,
                                                     challenge):
    class Runtime:
        run = staticmethod(raising(error))

    monkeypatch.setattr(GET_RUNTIME, lambda: Runtime())
    response = client.post("/api/chat", json={"message": "Tokyo recovery", "phase": 5})
    assert_sign_in_response(response, code, message, challenge)


@pytest.mark.parametrize("error, code, message, challenge", CASES)
def test_the_order_route_answers_the_sign_in_body(client, monkeypatch, error, code, message,
                                                  challenge):
    async def package(product_id):
        return {"available_sizes": ["7 nights"]}, {"product_id": "CTY-002", "price": 1000.0}

    monkeypatch.setattr(chat_mod, "_package_for_hold", package)
    monkeypatch.setattr(chat_mod, "_requested_duration", lambda row, size: "7 nights")
    monkeypatch.setattr(concierge_mod, "create_production_agent",
                        agent_failing("process_hold", error))
    response = client.post("/api/chat/order", json={
        "product_id": "CTY-002", "quantity": 2, "size": "7 nights", "phase": 4})
    assert_sign_in_response(response, code, message, challenge)


@pytest.mark.parametrize("error, code, message, challenge", CASES)
def test_the_book_route_answers_the_sign_in_body(client, monkeypatch, error, code, message,
                                                 challenge):
    async def traveler_booking(traveler_id, booking_id):
        return {"booking_id": "HLD-1", "package_id": "CTY-002", "duration": "5 nights",
                "total_amount": "4998.00", "travelers_count": 2}

    async def package(product_id):
        return {}, {"product_id": "CTY-002", "name": "Tokyo", "price": 2499.0}

    monkeypatch.setattr(chat_mod, "_traveler_booking", traveler_booking)
    monkeypatch.setattr(chat_mod, "_package_for_hold", package)
    monkeypatch.setattr(concierge_mod, "create_production_agent",
                        agent_failing("process_booking", error))
    response = client.post("/api/chat/book", json={"booking_id": "HLD-1"})
    assert_sign_in_response(response, code, message, challenge)


@pytest.mark.parametrize("error, code, message, challenge", CASES)
def test_the_journeys_route_answers_the_sign_in_body(client, monkeypatch, error, code, message,
                                                     challenge):
    class Runtime:
        stop_session = staticmethod(raising(error))

    async def stop_target(db, journey_id, owner):
        return "thread-1"

    monkeypatch.setattr(journeys, "_stop_target", stop_target)
    monkeypatch.setattr(journeys, "get_rds_data_client", lambda: object())
    monkeypatch.setattr(journeys, "get_workflow_runtime", lambda: Runtime())
    response = client.post("/api/journeys/jrn_x/stop-session")
    assert_sign_in_response(response, code, message, challenge)


def test_a_credential_error_never_echoes_its_text():
    assert credential_error(CallerTokenMissing("secret-detail")).detail == SIGN_IN_MESSAGE
    assert credential_error(CallerTokenExpired("secret-detail")).detail == MESSAGE


def sse_events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines()
            if line.startswith("data: ")]


@pytest.mark.parametrize("error, code, message, challenge", CASES)
def test_the_stream_route_ends_with_the_coded_error_event(client, monkeypatch, error, code,
                                                          message, challenge):
    monkeypatch.setattr(chat_mod, "production_search", raising(error))
    response = client.post("/api/chat/stream", json={"message": "Quiet beach week", "phase": 4})
    assert sse_events(response)[-1] == {"type": "error", "message": message, "code": code}
    assert not any(word in response.text.lower() for word in FORBIDDEN_WORDS)


@pytest.mark.parametrize("error, code, message, challenge", CASES)
async def test_a_credential_error_raised_inside_the_stream_carries_its_code(
    monkeypatch, caplog, error, code, message, challenge
):
    monkeypatch.setattr(chat_mod, "chat", raising(error))
    with caplog.at_level("INFO", logger=chat_mod.logger.name):
        response = await chat_mod.stream_chat(
            chat_mod.ChatRequest(message="Quiet beach week", phase=4), principal())
        events = [json.loads(frame[6:]) async for frame in response.body_iterator
                  if frame.startswith("data: ")]
    assert events[-1] == {"type": "error", "message": message, "code": code}
    assert error.__name__ in caplog.text
    assert "secret-detail" not in caplog.text
    assert not any(record.exc_info for record in caplog.records)
