"""A denied grant on the stop endpoint is 403, and nothing is stopped."""

import pytest
from fastapi import HTTPException

from backend.http_auth import HttpPrincipal
from backend.routers import journeys

JORDAN = HttpPrincipal("test", "trv_meridian_demo", "test")


class DeniedClient:
    def scoped_session(self, **_kwargs):
        raise PermissionError("Grant denied for this traveler.")


class Identity:
    def authorization_context(self):
        return None


class Runtime:
    def __init__(self):
        self.stops = []

    async def stop_session(self, traveler_id, thread_id):
        self.stops.append((traveler_id, thread_id))


async def test_a_denied_grant_is_403_and_nothing_is_stopped(monkeypatch):
    runtime = Runtime()
    monkeypatch.setattr(journeys, "get_rds_data_client", lambda: DeniedClient())
    monkeypatch.setattr(journeys, "get_workflow_runtime", lambda: runtime)
    monkeypatch.setattr(journeys, "get_agentcore_identity", lambda: Identity())
    with pytest.raises(HTTPException) as caught:
        await journeys.stop_session("jrn_x", JORDAN, None)
    assert caught.value.status_code == 403 and runtime.stops == []
