"""A backend 403 for another traveler's data is kept as a deny row, and the 403 stays the same."""

import asyncio
import logging

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import http_auth
from backend.http_auth import HttpPrincipal, authorize_traveler
from backend.main import app
from tests.access_audit_support import FakeAccessWriter

DECOY = "trv_demo_decoy"
SIGNED_IN = "trv_meridian_demo"
REFUSED = "The authenticated caller is not authorized for that traveler."
REASON = "requested traveler is not the signed-in traveler"


@pytest.fixture
def client(monkeypatch):
    """The real app with a direct loopback caller signed in as Jordan."""
    for name in ("MERIDIAN_API_TOKEN", "MERIDIAN_API_TRAVELER_ID",
                 "MERIDIAN_ALLOW_INSECURE_LOCALHOST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setattr(http_auth, "get_cognito_verifier", lambda: None)
    return TestClient(app)


def deny_row(traveler_id=DECOY, reason=REASON):
    return {"provider": "loopback-development", "subject_id": "local-workshop",
            "principal": f"traveler:{SIGNED_IN}", "traveler_id": traveler_id,
            "decision": "deny", "reason": reason}


ROUTES = [
    ("POST", "/api/chat/stream", {"json": {"message": "x", "phase": 4, "customer_id": DECOY}}),
    ("POST", "/api/chat", {"json": {"message": "x", "phase": 4, "customer_id": DECOY}}),
    ("POST", "/api/chat/book", {"json": {"booking_id": "HLD-1", "traveler_id": DECOY}}),
    ("POST", "/api/chat/order",
     {"json": {"product_id": "CTY-002", "phase": 4, "traveler_id": DECOY}}),
    ("POST", "/api/diagnostics/rls-probe", {"json": {"traveler_id": DECOY}}),
    ("POST", "/api/diagnostics/session-receipt", {"json": {"traveler_id": DECOY}}),
    ("GET", "/api/journeys", {"params": {"traveler_id": DECOY}}),
    ("GET", "/api/journeys/jrn_test", {"params": {"traveler_id": DECOY}}),
    ("POST", "/api/journeys/jrn_test/stop-session", {"params": {"traveler_id": DECOY}}),
    ("GET", f"/api/memory/{DECOY}", {}),
    ("PATCH", f"/api/memory/{DECOY}/facts/home_airport", {"json": {"value": "JFK"}}),
    ("DELETE", f"/api/memory/{DECOY}/facts/home_airport", {}),
]


@pytest.mark.parametrize(("method", "path", "kwargs"), ROUTES,
                         ids=[f"{method} {path}" for method, path, _ in ROUTES])
def test_every_refusing_route_writes_exactly_one_deny_row(client, access_writer, method, path,
                                                          kwargs):
    response = client.request(method, path, **kwargs)

    assert (response.status_code, response.json()["error"]) == (403, REFUSED)
    assert access_writer.rows == [deny_row()]


def test_a_signed_in_callers_row_names_their_provider_subject_and_own_traveler(access_writer):
    decoy = HttpPrincipal("sub-decoy", DECOY, "cognito")

    with pytest.raises(HTTPException) as refused:
        asyncio.run(authorize_traveler(decoy, SIGNED_IN))

    assert (refused.value.status_code, refused.value.detail) == (403, REFUSED)
    assert access_writer.rows == [{"provider": "cognito", "subject_id": "sub-decoy",
                                   "principal": f"traveler:{DECOY}", "traveler_id": SIGNED_IN,
                                   "decision": "deny", "reason": REASON}]


@pytest.mark.parametrize("requested", [None, "", "me", SIGNED_IN])
def test_the_callers_own_traveler_writes_nothing(access_writer, requested):
    jordan = HttpPrincipal("sub-jordan", SIGNED_IN, "cognito")
    assert asyncio.run(authorize_traveler(jordan, requested)) == SIGNED_IN
    assert access_writer.rows == []


def test_an_over_long_id_is_stored_cut_to_the_column_and_said_so(client, access_writer):
    requested = "trv_" + "x" * 70

    response = client.get(f"/api/memory/{requested}")

    assert (response.status_code, response.json()["error"]) == (403, REFUSED)
    assert access_writer.rows == [deny_row(
        requested[:50], f"{REASON}; requested id truncated to 50 of 74 characters")]


def test_a_failed_audit_write_still_refuses_and_is_logged(client, monkeypatch, caplog):
    monkeypatch.setattr(http_auth, "get_rds_data_client", lambda: FakeAccessWriter(fails=True))

    with caplog.at_level(logging.ERROR, logger="backend.http_auth"):
        response = client.get(f"/api/memory/{DECOY}")

    assert (response.status_code, response.json()["error"]) == (403, REFUSED)
    (record,) = [r for r in caplog.records if r.name == "backend.http_auth"]
    assert record.levelno == logging.ERROR
    assert "traveler_access_audit" in record.getMessage()
    assert repr(DECOY) in record.getMessage() and "Aurora is unreachable" in record.getMessage()
