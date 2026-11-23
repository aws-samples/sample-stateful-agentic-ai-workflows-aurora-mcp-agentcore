"""The hold is placed at the price the traveler saw, or it is not placed (audit item 7)."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import backend.routers.chat as chat_router
from backend.agents.phase_04_production import production_agent as production_agent_mod
from backend.http_auth import HttpPrincipal
from backend.product_shape import row_to_api_product
from tests.test_audit_outcome import HOLD_TOOL, before, failed
from tests.test_production_transaction_boundaries import build_agent, runtime_decision
from tests.test_runtime_trace_hooks import _answer, _traced_turn

PRICE_MISMATCH = "hold_price_mismatch"
DECOY = HttpPrincipal("sub-decoy", "trv_demo_decoy", "cognito")


def package(price_cents: int, durations=("7 nights",)) -> dict:
    return {"package_id": "CTY-002", "name": "Tokyo Culture & Cuisine",
            "price_per_person": price_cents / 100, "durations": list(durations),
            "operator": "JAL Premium", "trip_type": "City Breaks"}


def quote(unit_cents: int = 250000, quantity: int = 2, size: str = "7 nights", **overrides) -> dict:
    terms = {"unit_price_cents": unit_cents, "total_cents": unit_cents * quantity,
             "currency": "USD", "size": size, "quantity": quantity}
    return {**terms, **overrides}


def order_request(quote_terms=None, **overrides):
    fields = {"product_id": "CTY-002", "size": "7 nights", "quantity": 2, "phase": 4,
              "conversation_id": "conv_hold_1", **overrides}
    if quote_terms is not None:
        fields["quote"] = quote_terms
    return chat_router.OrderRequest(**fields)


def placed(unit_cents: int = 250000, quantity: int = 2, **overrides):
    """The Runtime's decision for a hold the Lambda wrote (or replayed) at ``unit_cents``."""
    hold = {"bookingId": "HLD-1", "status": "held", "expiresAt": "2026-11-20T00:00:00+00:00",
            "totalAmount": f"{unit_cents * quantity / 100:.2f}", "seatsRemaining": 2,
            **overrides}
    return runtime_decision(message="Held.", packages=[], hold=hold, policy_decision="allow")


def price_refused():
    """What the Runtime reports when the holds Lambda answers hold_price_mismatch."""
    return runtime_decision(
        message="That price changed.", packages=[], hold=None, hold_refused=PRICE_MISMATCH,
        hold_error=PRICE_MISMATCH,
        activities=[before("c1", HOLD_TOOL), failed("c1", HOLD_TOOL, PRICE_MISMATCH)],
    )


class Catalog:
    """The catalog as the backend reads it: its price can move between reads."""

    def __init__(self, price_cents: int, durations=("7 nights",)):
        self.price_cents = price_cents
        self.durations = durations

    async def read(self, _product_id):
        row = package(self.price_cents, self.durations)
        return row, row_to_api_product(row)


def wire(monkeypatch, decision, catalog):
    """The real hold route over a fake Phase 4 agent and catalog; returns the Runtime calls."""
    calls: list = []
    agent = build_agent([], decision, calls)
    monkeypatch.setattr(production_agent_mod, "require_agentcore_platform", lambda **_kw: None)
    monkeypatch.setattr(production_agent_mod, "create_production_agent", lambda: agent)
    monkeypatch.setattr(chat_router, "_package_for_hold", catalog.read)
    return calls


def hold(request):
    return asyncio.run(chat_router.production_hold(request))


def refused_with(request) -> HTTPException:
    with pytest.raises(HTTPException) as answered:
        hold(request)
    return answered.value


def test_an_unchanged_price_holds_in_one_request(monkeypatch):
    calls = wire(monkeypatch, placed(), Catalog(250000))

    reply = hold(order_request(quote()))

    assert reply.order.status == "held"
    assert reply.order.total == 5000.0
    assert calls[0][1]["hold_target"]["unit_price_cents"] == 250000


def test_a_price_change_between_display_and_click_is_409_and_creates_no_hold(monkeypatch):
    calls = wire(monkeypatch, price_refused(), Catalog(260000))

    answered = refused_with(order_request(quote(250000)))

    assert answered.status_code == 409
    assert answered.detail["code"] == "price_changed"
    assert answered.detail["quote"] == quote(260000)
    assert calls[0][1]["hold_target"]["unit_price_cents"] == 250000, "the quoted price is asked"


def test_the_renewed_approval_holds_at_the_new_price(monkeypatch):
    calls = wire(monkeypatch, placed(260000), Catalog(260000))

    reply = hold(order_request(quote(260000)))

    assert calls[0][1]["hold_target"]["unit_price_cents"] == 260000
    assert reply.order.total == 5200.0
    assert reply.order.items[0].unit_price == 2600.0


def test_a_lost_reply_retried_with_the_original_quote_replays_the_original_amount(monkeypatch):
    """The Lambda replays the committed request; the reply reports what it reserved."""
    calls = wire(monkeypatch, placed(250000, replayed=True), Catalog(260000))

    reply = hold(order_request(quote(250000)))

    assert calls[0][1]["hold_target"]["unit_price_cents"] == 250000
    assert reply.order.total == 5000.0
    assert reply.order.items[0].unit_price == 2500.0


def test_the_order_reports_the_reserved_amount_not_a_fresh_catalog_read(monkeypatch):
    wire(monkeypatch, placed(250000), Catalog(999900))

    reply = hold(order_request(quote(250000)))

    assert (reply.order.total, reply.order.items[0].unit_price) == (5000.0, 2500.0)


def test_an_older_client_without_a_quote_gets_409_quote_required_and_no_hold(monkeypatch):
    calls = wire(monkeypatch, placed(), Catalog(250000))

    answered = refused_with(order_request())

    assert answered.status_code == 409
    assert answered.detail["code"] == "quote_required"
    assert answered.detail["quote"] == quote(250000)
    assert calls == [], "an unseen price is never treated as approved"


@pytest.mark.parametrize("wrong", [
    {"size": "10 nights"}, {"quantity": 3}, {"total_cents": 500001},
], ids=["duration", "party size", "total"])
def test_a_quote_that_disagrees_with_the_request_is_422_and_creates_no_hold(monkeypatch, wrong):
    calls = wire(monkeypatch, placed(), Catalog(250000, ("7 nights", "10 nights")))

    answered = refused_with(order_request(quote(**wrong)))

    assert answered.status_code == 422
    assert calls == []


def test_a_quote_in_another_currency_is_rejected_when_the_request_is_read():
    with pytest.raises(ValidationError):
        order_request(quote(currency="EUR"))


@pytest.mark.parametrize("wrong", [
    {"unit_price_cents": 0, "total_cents": 0}, {"unit_price_cents": -5, "total_cents": -10},
], ids=["zero", "negative"])
def test_a_quote_must_name_a_positive_price(wrong):
    with pytest.raises(ValidationError):
        order_request(quote(**wrong))


async def test_the_decoy_without_a_quote_is_refused_as_forbidden_before_the_quote(
        monkeypatch, access_writer):
    governed = AsyncMock()
    monkeypatch.setattr(chat_router, "production_hold", governed)

    with pytest.raises(HTTPException) as answered:
        await chat_router.process_order(order_request(traveler_id="trv_meridian_demo"), DECOY)

    assert answered.value.status_code == 403
    governed.assert_not_awaited()


def test_the_lambda_refusal_reaches_the_backend_as_the_raw_error_code(monkeypatch):
    decision = _traced_turn(monkeypatch, [(HOLD_TOOL, _answer({"error": PRICE_MISMATCH}))])

    assert decision.hold is None
    assert decision.hold_error == PRICE_MISMATCH


def test_the_409_body_carries_the_sentence_the_code_and_the_current_terms():
    from backend.hold_quote import HoldQuoteReply

    reply = HoldQuoteReply("price_changed", "The price changed.", quote(260000))

    assert reply.status_code == 409
    assert reply.detail == {"code": "price_changed", "message": "The price changed.",
                            "quote": quote(260000)}
    assert reply.body() == {"error": "The price changed.", "code": "price_changed",
                            "quote": quote(260000)}


def test_the_plain_route_answers_with_that_body():
    from backend.hold_quote import HoldQuoteReply
    from backend.main import http_exception_handler

    response = asyncio.run(http_exception_handler(
        None, HoldQuoteReply("quote_required", "Review the price.", quote())))

    assert response.status_code == 409
    assert json.loads(response.body) == {
        "error": "Review the price.", "code": "quote_required", "quote": quote()}


def test_the_stream_route_answers_with_that_event():
    from backend.hold_quote import HoldQuoteReply

    event = chat_router._stream_error_event(
        HoldQuoteReply("price_changed", "The price changed.", quote(260000)))

    assert event == {"type": "error", "message": "The price changed.", "status": 409,
                     "code": "price_changed", "quote": quote(260000)}


def test_the_demo_validator_quotes_the_catalog_row_it_read_for_each_attempt(monkeypatch):
    from scripts import validate_demo

    class Reply:
        def raise_for_status(self):
            return None

        def json(self):
            return {"price": 2499.99}

    reads = []
    monkeypatch.setattr(validate_demo, "client", type("C", (), {
        "get": staticmethod(lambda path: reads.append(path) or Reply())}), raising=False)

    assert validate_demo.hold_quote("CTY-002", "7 nights", 2) == {
        "unit_price_cents": 249999, "total_cents": 499998, "currency": "USD",
        "size": "7 nights", "quantity": 2}
    assert reads == ["/api/products/CTY-002"]


@pytest.mark.parametrize("route", ["/api/chat/order", "/api/chat/order/stream"])
@pytest.mark.parametrize("conversation", [None, ""])
def test_a_hold_without_a_conversation_id_is_refused_before_any_hold(
        monkeypatch, route, conversation):
    from fastapi.testclient import TestClient

    from backend import main
    from backend.http_auth import require_http_principal

    placed = AsyncMock()
    monkeypatch.setattr(chat_router, "production_hold", placed)
    monkeypatch.setitem(main.app.dependency_overrides, require_http_principal,
                        lambda: HttpPrincipal("sub-jordan", "trv_meridian_demo", "cognito"))
    body = {"product_id": "CTY-002", "size": "7 nights", "quantity": 2, "phase": 4,
            "quote": quote()}
    if conversation is not None:
        body["conversation_id"] = conversation

    response = TestClient(main.app, raise_server_exceptions=False).post(route, json=body)

    assert response.status_code == 422
    placed.assert_not_awaited()
