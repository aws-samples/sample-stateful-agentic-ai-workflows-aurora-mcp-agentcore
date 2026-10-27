"""A multi-step prompt is handed to Workflow before the Runtime is called, for both experiences."""

import json
from unittest.mock import AsyncMock

import pytest

import backend.routers.chat as chat_router
from backend.http_auth import HttpPrincipal
from backend.routers.chat import ChatRequest, chat
from backend.trace_names import BOUNDARY_CODE

PRINCIPAL = HttpPrincipal("test-client", "trv_meridian_demo", "test")
PROMPT = "My flight was canceled. Rework my Tokyo trip, then check availability."
NO_DESTINATION = (
    "My flight was canceled. Rework the trip, then check duration availability for the "
    "best three options."
)
DESTINATIONS = ["Tokyo", "Kyoto", "Lisbon", "Amalfi Coast"]


@pytest.fixture
def no_runtime(monkeypatch):
    production = AsyncMock(side_effect=AssertionError("the handoff is decided before the Runtime"))
    monkeypatch.setattr(chat_router, "production_search", production)
    monkeypatch.setattr(chat_router, "_catalog_destinations", AsyncMock(return_value=DESTINATIONS))
    return production


async def test_the_capability_phase_hands_off_without_calling_the_runtime(no_runtime):
    response = await chat(ChatRequest(message=PROMPT, phase=4), PRINCIPAL)

    assert "Tokyo" in response.message and "Switch to Workflow" in response.message
    assert "checkpoint" not in response.message.lower()
    assert response.products is None and response.memory_facts is None
    assert "Run this in Workflow" in response.follow_ups
    span = next(a for a in response.activities if a.title == "Saved-step workflow required")
    assert span.agent_name == BOUNDARY_CODE
    no_runtime.assert_not_awaited()


async def test_the_concierge_experience_names_the_destination_too(no_runtime):
    response = await chat(ChatRequest(
        message=PROMPT, phase=4, experience="concierge", conversation_id="current-chat"), PRINCIPAL)

    assert "your Tokyo trip" in response.message and "hold" in response.message
    assert response.recovery_request == PROMPT and response.conversation_id == "current-chat"
    assert not any(word in response.message.lower() for word in ["workflow", "phase", "checkpoint"])
    no_runtime.assert_not_awaited()


async def test_the_destination_is_the_one_the_traveler_wrote_not_one_from_memory(no_runtime):
    response = await chat(ChatRequest(
        message="My flight was canceled. Rework my Lisbon trip, then check availability.",
        phase=4), PRINCIPAL)
    assert "Lisbon" in response.message and "Tokyo" not in response.message


async def test_no_named_destination_means_no_invented_one(no_runtime):
    response = await chat(ChatRequest(message=NO_DESTINATION, phase=4), PRINCIPAL)
    assert "your trip" in response.message
    assert not any(d in response.message for d in DESTINATIONS)


async def test_a_failed_catalog_read_still_hands_off_with_plain_wording(monkeypatch):
    production = AsyncMock(side_effect=AssertionError("no Runtime call"))
    monkeypatch.setattr(chat_router, "production_search", production)
    monkeypatch.setattr(chat_router, "_catalog_destinations",
                        AsyncMock(side_effect=RuntimeError("Aurora unreachable")))

    response = await chat(ChatRequest(message=PROMPT, phase=4), PRINCIPAL)

    assert "your trip" in response.message and "Switch to Workflow" in response.message
    production.assert_not_awaited()


async def test_with_traveler_context_off_nothing_is_read_and_nothing_hands_off(no_runtime):
    response = await chat(ChatRequest(message=PROMPT, phase=4, memory_enabled=False), PRINCIPAL)
    assert "Traveler context is off" in response.message
    chat_router._catalog_destinations.assert_not_awaited()


async def test_the_stream_for_a_handoff_never_calls_the_runtime_or_streams_prose(no_runtime):
    response = await chat_router.stream_chat(ChatRequest(message=PROMPT, phase=4), PRINCIPAL)

    frames = [frame async for frame in response.body_iterator]
    events = [json.loads(frame[len("data: "):]) for frame in frames if frame.startswith("data: ")]
    assert not any(event["type"] == "delta" for event in events)
    assert events[-1]["type"] == "complete"
    assert "Tokyo" in events[-1]["response"]["message"]
    no_runtime.assert_not_awaited()


@pytest.mark.parametrize(("query", "expected"), [
    ("Rework my Tokyo trip", "Tokyo"),
    ("rework my TOKYO trip", "Tokyo"),
    ("Kyoto extension after Tokyo", "Kyoto"),            # the first one written wins
    ("a Tokyoite weekend", None),                         # not a whole word
    ("Rework the Amalfi Coast week", "Amalfi Coast"),
    ("nothing named", None),
    ("", None),
])
def test_named_destination(query, expected):
    assert chat_router._named_destination(query, DESTINATIONS) == expected
