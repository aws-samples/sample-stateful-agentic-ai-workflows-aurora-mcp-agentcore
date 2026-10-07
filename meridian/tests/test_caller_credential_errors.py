"""A missing or expired caller token is a 401 on every route, never a 503 or a 500."""

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.agentcore.errors import (
    CallerCredentialError,
    CallerTokenExpired,
    CallerTokenMissing,
)
from backend.http_auth import HttpPrincipal, require_http_principal
from backend.routers import journeys

JORDAN = HttpPrincipal("test", "trv_meridian_demo", "test")


@pytest.mark.parametrize("error", [CallerTokenMissing, CallerTokenExpired])
def test_the_token_errors_are_not_runtime_errors(error):
    assert issubclass(error, CallerCredentialError)
    assert not issubclass(error, RuntimeError)


class _Identity:
    def authorization_context(self):
        return None


@pytest.mark.parametrize("error", [CallerTokenMissing, CallerTokenExpired])
def test_the_journeys_stop_route_answers_401_not_503(monkeypatch, error):
    class Runtime:
        async def stop_session(self, traveler_id, thread_id):
            raise error("token problem")

    async def stop_target(client, journey_id, owner):
        return "thread-1"

    monkeypatch.setattr(journeys, "_stop_target", stop_target)
    monkeypatch.setattr(journeys, "get_rds_data_client", lambda: object())
    monkeypatch.setattr(journeys, "get_workflow_runtime", lambda: Runtime())
    monkeypatch.setitem(main.app.dependency_overrides, require_http_principal, lambda: JORDAN)
    try:
        response = TestClient(main.app).post("/api/journeys/jrn_x/stop-session")
    finally:
        main.app.dependency_overrides.pop(require_http_principal, None)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "token problem" in response.json()["error"]
