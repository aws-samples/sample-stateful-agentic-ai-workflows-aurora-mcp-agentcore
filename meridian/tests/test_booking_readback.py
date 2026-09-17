"""Lost acknowledgements are reconciled with scoped reads, never model writes."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from backend.http_auth import HttpPrincipal
from backend.routers import chat as module

PRINCIPAL = HttpPrincipal("test", "traveler-a", "test")
LINE = {"booking_id": "HLD-saved", "status": "held", "total_amount": "4800.00",
        "unit_price": "2400.00", "travelers_count": 2, "duration": "7 nights",
        "package_id": "tokyo", "name": "Tokyo", "hold_expires_at": "2099-01-01 00:00:00+00",
        "hold_created_at": "2026-09-17 10:00:00+00", "confirmed_at": None}


@pytest.fixture
def db(monkeypatch):
    scopes = []
    @asynccontextmanager
    async def scoped_session(**kwargs):
        scopes.append(kwargs)
        yield "scoped-transaction"
    client = SimpleNamespace(scoped_session=scoped_session, execute=AsyncMock(return_value=[LINE]), scopes=scopes)
    monkeypatch.setattr(module, "get_rds_data_client", lambda: client)
    monkeypatch.setattr(module, "get_agentcore_identity", lambda: SimpleNamespace(
        scope_for_turn=lambda: SimpleNamespace(authorization="workload-grant")))
    return client


async def test_hold_lookup_binds_the_principal_and_exact_terms(db):
    response = await module.read_hold("pending-conversation", "tokyo", "7 nights", 2, PRINCIPAL)
    sql, params = db.execute.call_args.args
    assert sql.strip().startswith("SELECT")
    assert params == ("traveler-a", "traveler-a", "concierge:pending-conversation", "tokyo", "7 nights", 2)
    assert db.scopes[0]["traveler_id"] == "traveler-a"
    assert db.execute.call_args.kwargs["transaction_id"] == "scoped-transaction"
    assert response.order.order_id == "HLD-saved"
    assert response.order.total == 4800
    assert response.order.items[0].unit_price == 2400
    assert response.order.hold_created_at == "2026-09-17T10:00:00+00:00"


async def test_missing_hold_is_explicit_without_creating_one(db):
    db.execute.return_value = []
    response = await module.read_hold("pending", "tokyo", "7 nights", 2, PRINCIPAL)
    assert response.order is None
    db.execute.assert_awaited_once()


async def test_booking_lookup_rejects_an_invisible_booking(db):
    db.execute.return_value = []
    with pytest.raises(HTTPException) as exc:
        await module.read_booking("foreign", PRINCIPAL)
    assert exc.value.status_code == 404
    assert db.execute.call_args.args[1] == ("foreign", "traveler-a")


async def test_expired_hold_is_read_as_expired_without_changing_inventory(db):
    db.execute.return_value = [{**LINE, "hold_expires_at": "2000-01-01 00:00:00+00"}]
    response = await module.read_booking("HLD-saved", PRINCIPAL)
    assert response.order.status == "expired"
    db.execute.assert_awaited_once()


async def test_readback_marks_data_api_timestamps_as_utc(db):
    db.execute.return_value = [{**LINE, "hold_expires_at": "2099-01-01 00:00:00",
                               "confirmed_at": "2026-09-17 10:02:00"}]
    response = await module.read_booking("HLD-saved", PRINCIPAL)
    assert response.order.hold_expires_at == "2099-01-01T00:00:00+00:00"
    assert response.order.confirmed_at == "2026-09-17T10:02:00+00:00"


async def test_ambiguous_hold_lookup_refuses_to_select_a_receipt(db):
    db.execute.return_value = [LINE, LINE]
    with pytest.raises(HTTPException) as exc:
        await module.read_hold("pending", "tokyo", "7 nights", 2, PRINCIPAL)
    assert exc.value.status_code == 409


@pytest.mark.parametrize("path", [
    "/api/chat/bookings/HLD-saved",
    "/api/chat/holds?conversation_id=pending&product_id=tokyo&duration=7%20nights&quantity=2",
])
def test_revoked_workload_grant_returns_a_refusal_not_a_server_failure(db, path):
    from fastapi.testclient import TestClient
    from backend.authorization import AuthorizationDecision, TravelerAuthorizationError
    from backend.main import app

    @asynccontextmanager
    async def denied(**kwargs):
        raise TravelerAuthorizationError(AuthorizationDecision(
            allowed=False, decision="deny", traveler_id="traveler-a",
            provider="test", subject_id="revoked-workload", principal="test",
        ))
        yield  # pragma: no cover - satisfy the async context manager contract

    db.scoped_session = denied
    response = TestClient(app).get(path)
    assert response.status_code == 403
    assert "not authorized" in response.json()["error"]
    db.execute.assert_not_awaited()
