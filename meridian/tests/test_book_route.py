"""The book route reads the held booking under RLS, confirms through the runtime, and reports."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.agents.production_04 import concierge as concierge_mod
from backend.agents.production_04.concierge import BookingOutcome, BookingTarget
from backend.routers import chat as chat_mod
from backend.routers.chat import BookingRequest, production_booking

LINE = {
    "booking_id": "HLD-1", "status": "held", "total_amount": "4998.00",
    "hold_expires_at": "2026-09-11 00:00:00+00", "confirmed_at": None,
    "package_id": "CTY-002", "duration": "5 nights", "travelers_count": 2, "unit_price": "2499.00",
}
PACKAGE = {"product_id": "CTY-002", "name": "Tokyo Neon Nights", "price": 2499.0}


def _outcome(**overrides):
    base = dict(
        booking={"bookingId": "HLD-1", "status": "confirmed", "totalAmount": "4998.00",
                 "expiresAt": "2026-09-11 00:00:00+00", "createdAt": "2026-09-10 12:00:00+00",
                 "confirmedAt": "2026-09-10 13:00:00+00"},
        refused=None, policy_decision="allow", activities=[],
        message="Your Tokyo trip is confirmed.", conv_id="conv-1", trace_id="abc",
    )
    base.update(overrides)
    return BookingOutcome(**base)


@pytest.fixture
def wired(monkeypatch):
    seen = {}

    async def traveler_booking(traveler_id, booking_id):
        seen["read"] = (traveler_id, booking_id)
        return dict(LINE)

    async def package(product_id):
        seen["package"] = product_id
        return {}, dict(PACKAGE)

    def agent_factory(outcome):
        async def process_booking(traveler_id, conversation_id, target):
            seen["target"] = target
            seen["conversation_id"] = conversation_id
            return outcome
        return lambda: SimpleNamespace(process_booking=process_booking)

    monkeypatch.setattr(chat_mod, "_traveler_booking", traveler_booking)
    monkeypatch.setattr(chat_mod, "_package_for_hold", package)
    seen["use"] = lambda outcome: monkeypatch.setattr(
        concierge_mod, "create_production_agent", agent_factory(outcome)
    )
    return seen


def test_a_confirmed_booking_becomes_a_confirmed_order(wired):
    wired["use"](_outcome())
    request = BookingRequest(booking_id="HLD-1", conversation_id="conv-1")

    response = asyncio.run(production_booking(request))

    assert wired["read"] == ("trv_meridian_demo", "HLD-1")
    assert wired["package"] == "CTY-002"
    assert wired["target"] == BookingTarget(
        booking_id="HLD-1", total_cents=499800, package_id="CTY-002",
        duration="5 nights", travelers=2,
    )
    assert wired["conversation_id"] == "conv-1"
    order = response.order
    assert order.order_id == "HLD-1" and order.status == "confirmed"
    assert order.total == 4998.0 and order.payment_required is False
    assert order.confirmed_at == "2026-09-10 13:00:00+00"
    assert order.hold_created_at == "2026-09-10 12:00:00+00"
    assert order.items[0].size == "5 nights" and order.items[0].quantity == 2
    assert response.message == "Your Tokyo trip is confirmed."


def test_a_refused_booking_returns_the_reason_and_no_order(wired):
    wired["use"](_outcome(booking=None, refused="hold_expired", policy_decision=None,
                          message="That hold has expired, so nothing was booked."))

    response = asyncio.run(production_booking(BookingRequest(booking_id="HLD-1")))

    assert response.order is None
    assert "expired" in response.message


def test_an_unknown_booking_is_a_404(monkeypatch):
    async def missing(traveler_id, booking_id):
        raise HTTPException(status_code=404, detail="That booking is not on record for you.")

    monkeypatch.setattr(chat_mod, "_traveler_booking", missing)
    with pytest.raises(HTTPException) as failure:
        asyncio.run(production_booking(BookingRequest(booking_id="HLD-404")))
    assert failure.value.status_code == 404


def test_a_platform_failure_is_a_503(wired, monkeypatch):
    async def broken(traveler_id, conversation_id, target):
        raise RuntimeError("runtime unreachable")

    monkeypatch.setattr(
        concierge_mod, "create_production_agent",
        lambda: SimpleNamespace(process_booking=broken),
    )
    with pytest.raises(HTTPException) as failure:
        asyncio.run(production_booking(BookingRequest(booking_id="HLD-1")))
    assert failure.value.status_code == 503
