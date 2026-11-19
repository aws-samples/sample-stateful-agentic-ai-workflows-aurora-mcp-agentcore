"""validate_demo builds the Hold quote from the catalog row, and a failed read is a failed step."""

import httpx
import pytest

from scripts import validate_demo


@pytest.fixture
def catalog(monkeypatch):
    """A stub backend whose catalog answers with ``state['status']``; no network."""
    state = {"status": 200, "price": "1299.00"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/products/CTY-002":
            if state["status"] != 200:
                return httpx.Response(state["status"], json={"detail": "down"})
            return httpx.Response(200, json={"price": state["price"]})
        return httpx.Response(200, json={"order": {"order_id": "b1"}})

    monkeypatch.setattr(validate_demo, "client", raising=False, value=httpx.Client(
        base_url="http://stub", transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(validate_demo, "steps", [])
    return state


def test_hold_quote_is_the_catalog_unit_price_times_the_quantity(catalog):
    assert validate_demo.hold_quote("CTY-002", "7 nights", 2) == {
        "unit_price_cents": 129900, "total_cents": 259800, "currency": "USD",
        "size": "7 nights", "quantity": 2}


def test_a_catalog_read_failure_is_a_recorded_failed_step_not_a_traceback(catalog):
    catalog["status"] = 503

    body, status = validate_demo.record(
        "p4_order_hold", "POST", "/api/chat/order",
        lambda: {"quote": validate_demo.hold_quote("CTY-002", "7 nights", 2)},
        ok=lambda b, s: s == 200)

    step = validate_demo.steps[-1]
    assert status is None and "HTTPStatusError" in body["exception"]
    assert step["checks"] == {"ok": False} and step["request"] is None


def test_a_payload_factory_that_succeeds_is_sent_and_recorded(catalog):
    _body, status = validate_demo.record(
        "p4_order_hold", "POST", "/api/chat/order",
        lambda: {"quote": validate_demo.hold_quote("CTY-002", "7 nights", 2)},
        ok=lambda b, s: s == 200)

    assert status == 200
    assert validate_demo.steps[-1]["request"]["quote"]["total_cents"] == 259800
