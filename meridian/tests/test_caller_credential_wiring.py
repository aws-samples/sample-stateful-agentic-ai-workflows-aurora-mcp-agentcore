"""The 401 body for caller token errors is constant, and the middleware is wired in production."""

import logging

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.agentcore.caller_credential import CallerCredentialMiddleware
from backend.agentcore.errors import CallerTokenExpired, CallerTokenMissing
from backend.http_auth import HttpPrincipal, require_http_principal
from backend.routers import journeys
from backend.token_expiry import MESSAGE as EXPIRED_BODY
from backend.token_expiry import SIGN_IN_MESSAGE as BODY

JORDAN = HttpPrincipal("test", "trv_meridian_demo", "test")
REMOTE_TEXT = "remote runtime said: secret-detail-123"


@pytest.mark.parametrize("error", [CallerTokenMissing, CallerTokenExpired])
def test_the_401_body_is_constant_and_the_exception_text_is_only_logged_by_class(
    monkeypatch, caplog, error
):
    class Runtime:
        async def stop_session(self, traveler_id, thread_id):
            raise error(REMOTE_TEXT)

    async def stop_target(client, journey_id, owner):
        return "thread-1"

    monkeypatch.setattr(journeys, "_stop_target", stop_target)
    monkeypatch.setattr(journeys, "get_rds_data_client", lambda: object())
    monkeypatch.setattr(journeys, "get_workflow_runtime", lambda: Runtime())
    monkeypatch.setitem(main.app.dependency_overrides, require_http_principal, lambda: JORDAN)
    caplog.set_level(logging.INFO)
    response = TestClient(main.app).post("/api/journeys/jrn_x/stop-session")
    assert response.status_code == 401
    assert response.json()["error"] == (EXPIRED_BODY if error is CallerTokenExpired else BODY)
    assert "secret-detail-123" not in response.text
    assert "secret-detail-123" not in caplog.text
    assert error.__name__ in caplog.text


def test_the_caller_credential_middleware_is_installed_outside_cors():
    classes = [m.cls for m in main.app.user_middleware]
    assert CallerCredentialMiddleware in classes
    assert classes.index(CallerCredentialMiddleware) < classes.index(main.CORSMiddleware)


def test_a_cors_preflight_still_succeeds_through_the_middleware():
    response = TestClient(main.app).options(
        "/api/journeys/jrn_x/stop-session",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
