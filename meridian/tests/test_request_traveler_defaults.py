"""A hold or booking request that names no traveler is the signed-in traveler's, never Jordan's."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from backend.http_auth import CURRENT_TRAVELER, HttpPrincipal
from backend.routers import chat

JORDAN = HttpPrincipal("sub-jordan", "trv_meridian_demo", "cognito")
DECOY = HttpPrincipal("sub-decoy", "trv_demo_decoy", "cognito")


def test_neither_request_defaults_to_a_named_traveler():
    assert chat.OrderRequest(product_id="CTY-002", phase=4).traveler_id == CURRENT_TRAVELER
    assert chat.BookingRequest(booking_id="HLD-1").traveler_id == CURRENT_TRAVELER


@pytest.mark.parametrize("principal", [JORDAN, DECOY])
async def test_an_order_without_a_traveler_is_placed_for_the_signed_in_traveler(
        monkeypatch, principal):
    governed = AsyncMock(return_value=chat.OrderResponse(message="ok", activities=[]))
    monkeypatch.setattr(chat, "production_hold", governed)

    await chat.process_order(chat.OrderRequest(product_id="CTY-002", phase=4), principal)

    assert governed.call_args.args[0].traveler_id == principal.traveler_id


@pytest.mark.parametrize("principal", [JORDAN, DECOY])
async def test_a_booking_without_a_traveler_is_confirmed_for_the_signed_in_traveler(
        monkeypatch, principal):
    governed = AsyncMock(return_value=chat.BookingResponse(message="ok", activities=[]))
    monkeypatch.setattr(chat, "production_booking", governed)

    await chat.confirm_booking(chat.BookingRequest(booking_id="HLD-1"), principal)

    assert governed.call_args.args[0].traveler_id == principal.traveler_id


async def test_the_decoy_naming_jordan_is_refused_and_nothing_runs(monkeypatch):
    governed = AsyncMock()
    monkeypatch.setattr(chat, "production_hold", governed)
    monkeypatch.setattr(chat, "production_booking", governed)

    with pytest.raises(HTTPException) as order:
        await chat.process_order(chat.OrderRequest(
            product_id="CTY-002", phase=4, traveler_id=JORDAN.traveler_id), DECOY)
    with pytest.raises(HTTPException) as booking:
        await chat.confirm_booking(chat.BookingRequest(
            booking_id="HLD-1", traveler_id=JORDAN.traveler_id), DECOY)

    assert order.value.status_code == booking.value.status_code == 403
    governed.assert_not_awaited()
