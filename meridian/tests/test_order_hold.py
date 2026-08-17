"""Courtesy holds validate inventory and persist through the atomic DB function."""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.http_auth import HttpPrincipal
from backend.routers import chat as chat_mod
from backend.routers.chat import OrderRequest, process_order


class FakeDB:
    def __init__(self, product, hold_error=None):
        self.product = product
        self.hold_error = hold_error
        self.calls = []

    async def execute(self, sql, params=None, transaction_id=None):
        self.calls.append((sql, params, transaction_id))
        if "FROM trip_packages" in sql:
            return [self.product]
        if "create_courtesy_hold" in sql:
            if self.hold_error:
                raise RuntimeError(self.hold_error)
            return [
                {
                    "seats_available": 4,
                    "seats_reserved": 2,
                    "seats_remaining": 2,
                }
            ]
        raise AssertionError(f"Unexpected SQL: {sql}")

    @asynccontextmanager
    async def scoped_session(self, **_kwargs):
        yield "tx-hold"


PRODUCT = {
    "package_id": "TKY-003",
    "name": "Tokyo Executive Stopover",
    "operator": "Meridian Partner",
    "price_per_person": 1499.0,
    "description": "Tokyo stopover",
    "image_url": "/travel/TKY-003.jpg",
    "trip_type": "Business Travel",
    "durations": ["2 nights", "4 nights"],
    "availability": {"2 nights": 4, "4 nights": 2},
}

PRINCIPAL = HttpPrincipal(
    subject_id="test-client",
    traveler_id="trv_meridian_demo",
    authentication="test",
)


def _identity():
    return SimpleNamespace(
        authorization_context=lambda: SimpleNamespace(
            provider="test",
            subject_id="test-client",
            principal="test-client",
        )
    )


def test_hold_uses_published_duration_and_atomic_function(monkeypatch):
    db = FakeDB(PRODUCT)
    monkeypatch.setattr(chat_mod, "get_rds_data_client", lambda: db)
    monkeypatch.setattr(chat_mod, "get_agentcore_identity", _identity)

    response = asyncio.run(
        process_order(
            OrderRequest(
                product_id="TKY-003",
                size="2 nights",
                quantity=2,
                phase=4,
            ),
            PRINCIPAL,
        )
    )

    assert response.order is not None
    assert response.order.items[0].size == "2 nights"
    hold_call = next(call for call in db.calls if "create_courtesy_hold" in call[0])
    assert hold_call[1][3:5] == ("2 nights", 2)
    assert "%s::INTEGER" in hold_call[0]
    assert "Remaining package places: 2" in response.message


def test_hold_rejects_unknown_duration(monkeypatch):
    db = FakeDB(PRODUCT)
    monkeypatch.setattr(chat_mod, "get_rds_data_client", lambda: db)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            process_order(
                OrderRequest(
                    product_id="TKY-003",
                    size="9 nights",
                    quantity=1,
                    phase=4,
                ),
                PRINCIPAL,
            )
        )

    assert exc.value.status_code == 422
    assert not any("create_courtesy_hold" in call[0] for call in db.calls)


def test_hold_maps_atomic_oversell_rejection_to_conflict(monkeypatch):
    db = FakeDB(PRODUCT, hold_error="insufficient_inventory")
    monkeypatch.setattr(chat_mod, "get_rds_data_client", lambda: db)
    monkeypatch.setattr(chat_mod, "get_agentcore_identity", _identity)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            process_order(
                OrderRequest(
                    product_id="TKY-003",
                    size="2 nights",
                    quantity=2,
                    phase=4,
                ),
                PRINCIPAL,
            )
        )

    assert exc.value.status_code == 409
