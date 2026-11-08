"""Domain requests retain the records and locations the traveler actually named."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from backend.routers import chat


@pytest.fixture
def client(monkeypatch):
    client = AsyncMock()
    client.call.return_value = []

    @asynccontextmanager
    async def session():
        yield client

    monkeypatch.setattr(chat, "concierge_mcp_session", session)
    monkeypatch.setattr(chat, "_flagship_package_ids", AsyncMock(return_value=["ADV-001"]))
    monkeypatch.setattr(chat, "_hydrate_compared", AsyncMock(return_value=[]))
    return client


async def test_explicit_package_ids_are_compared_without_substitution(client):
    await chat._call_domain_tool("Compare CTY-002 and BCH-003", traveler_id="traveler")
    client.call.assert_awaited_once_with(
        "compare_packages", {"package_ids": ["CTY-002", "BCH-003"]}
    )
    chat._flagship_package_ids.assert_not_awaited()


@pytest.mark.parametrize(
    "query",
    [
        "What is the price range for Atlantis?",
        "How many trips in Antarctica?",
        "Compare CTY-002",
        "Compare my favorite trips",
        "Convert my budget to euros",
    ],
)
async def test_missing_domain_arguments_ask_for_details_before_any_tool_call(client, query):
    _rows, reply, answered = await chat._concierge_mcp_turn(query, "traveler", [], [])
    assert "Please" in reply
    assert not answered
    client.call.assert_not_awaited()


async def test_trip_type_prompt_keeps_its_explicit_stratified_comparison(client):
    await chat._call_domain_tool("Compare three trip types", traveler_id="traveler")
    client.call.assert_awaited_once_with("compare_packages", {"package_ids": ["ADV-001"]})


async def test_standalone_conversion_uses_the_requested_amount(client):
    await chat._call_domain_tool("Convert 1200 US dollars to euros", traveler_id="traveler")
    client.call.assert_awaited_once_with(
        "currency_convert", {"amount": 1200.0, "from_currency": "USD", "to_currency": "EUR"}
    )


async def test_empty_comparison_does_not_invent_an_amount_to_convert(client):
    _rows, reply, answered = await chat._concierge_mcp_turn(
        "Compare CTY-002 and BCH-003 in euros",
        "traveler",
        [],
        [],
    )
    assert answered and "No packages to compare" in reply
    client.call.assert_awaited_once_with(
        "compare_packages",
        {"package_ids": ["CTY-002", "BCH-003"]},
    )


async def test_a_missing_region_blocks_only_the_inventory_route(client):
    client.call.return_value = [{"package_id": "CTY-002"}]
    _rows, reply, answered = await chat._concierge_mcp_turn(
        "Compare CTY-002 and BCH-003, how many places are left?", "traveler", [], [])
    client.call.assert_awaited_once_with(
        "compare_packages", {"package_ids": ["CTY-002", "BCH-003"]})
    assert answered
    assert "Please name one supported location" in reply


async def test_a_missing_destination_blocks_only_the_price_range_route(client):
    await chat._call_domain_tool(
        "What is the price range, and what are my Bonvoy points?", traveler_id="traveler")
    client.call.assert_awaited_once_with(
        "loyalty_balance", {"travelerId": "traveler", "program": "Marriott Bonvoy"})


async def test_a_missing_destination_does_not_block_a_comparison(client):
    await chat._call_domain_tool(
        "Compare CTY-002 and BCH-003 and show the price range", traveler_id="traveler")
    client.call.assert_awaited_once_with(
        "compare_packages", {"package_ids": ["CTY-002", "BCH-003"]})


@pytest.mark.parametrize("query", ["Convert $, to euros", "Convert $,, to euros"])
async def test_a_currency_sign_with_no_digits_asks_for_a_usd_amount(client, query):
    _rows, reply, answered = await chat._concierge_mcp_turn(query, "traveler", [], [])
    assert "Please supply a USD amount" in reply
    assert not answered
    client.call.assert_not_awaited()
