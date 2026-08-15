"""Regression tests for the Phase 4 -> Workflow handoff prompt."""

import asyncio

from backend.routers.chat import (
    ChatRequest,
    MemoryFact,
    Product,
    _PHASE4_WORKFLOW_TRANSITION_MESSAGE,
    _needs_checkpointed_workflow,
    chat,
)


def test_disruption_replan_bridges_to_workflow() -> None:
    query = (
        "My JFK-to-Tokyo flight was cancelled. Rework the trip, then check "
        "duration availability for the best three options."
    )

    assert _needs_checkpointed_workflow(query)
    assert (
        _PHASE4_WORKFLOW_TRANSITION_MESSAGE
        == "I can carry forward your Tokyo context, but this needs two dependent "
        "steps: rework the itinerary, then check duration availability for the "
        "best three options. Switch to Workflow so each step is explicit, "
        "checkpointed, and resumable."
    )


def test_kyoto_extension_still_bridges_to_workflow() -> None:
    query = (
        "Plan the Kyoto extension: find matching packages, then verify "
        "available duration options."
    )

    assert _needs_checkpointed_workflow(query)


def test_simple_availability_query_stays_on_package_agent_path() -> None:
    assert not _needs_checkpointed_workflow("What dates are available for Tokyo?")


def test_phase4_demo_query_returns_workflow_handoff(monkeypatch) -> None:
    async def fake_production_search(*args, **kwargs):
        return (
            [
                Product(
                    product_id="TKY-001",
                    name="Tokyo Indie Neighborhood Walk",
                    brand="Nippon Local",
                    price=1599.0,
                    description="Tokyo neighborhood trip",
                    image_url="",
                    category="City Breaks",
                    similarity=0.44,
                ),
                Product(
                    product_id="LON-001",
                    name="London Executive Quick Trip",
                    brand="Meridian Select",
                    price=1899.0,
                    description="London business trip",
                    image_url="",
                    category="City Breaks",
                    similarity=0.31,
                )
            ],
            [],
            "raw production success",
            "conv-demo",
            [
                MemoryFact(
                    key="trip_goal",
                    value="Tokyo culture trip Oct 12-19",
                    source="profile",
                    confidence=0.9,
                )
            ],
        )

    monkeypatch.setattr("backend.routers.chat.production_search", fake_production_search)

    response = asyncio.run(
        chat(
            ChatRequest(
                phase=4,
                customer_id="trv_meridian_demo",
                message=(
                    "My JFK-to-Tokyo flight was cancelled. Rework the trip, then "
                    "check duration availability for the best three options."
                ),
            )
        )
    )

    assert response.message == _PHASE4_WORKFLOW_TRANSITION_MESSAGE
    assert response.conversation_id == "conv-demo"
    assert response.products
    assert [product.name for product in response.products] == [
        "Tokyo Indie Neighborhood Walk"
    ]
    assert any(a.title == "Checkpointed workflow required" for a in response.activities)


def test_phase4_memory_off_stops_before_recall_or_writeback(monkeypatch) -> None:
    called = False

    async def unexpected_production_search(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("production_search must not run while memory is disabled")

    monkeypatch.setattr(
        "backend.routers.chat.production_search",
        unexpected_production_search,
    )

    response = asyncio.run(
        chat(
            ChatRequest(
                phase=4,
                customer_id="trv_meridian_demo",
                memory_enabled=False,
                message=(
                    "Recall my October Tokyo plan and use my saved preferences "
                    "to recommend the next step."
                ),
            )
        )
    )

    assert not called
    assert response.products is None
    assert response.memory_facts is None
    assert "Traveler context is off" in response.message
    assert "Nothing was read from or written to memory" in response.message
    assert any(
        activity.title == "Traveler memory disabled for this run"
        for activity in response.activities
    )
