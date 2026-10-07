"""Regression tests for the Phase 4 -> Workflow handoff prompt."""

import asyncio

import pytest

from backend.http_auth import HttpPrincipal
from backend.routers.chat import (
    ChatRequest,
    MemoryFact,
    Product,
    _PHASE4_WORKFLOW_TRANSITION_MESSAGE,
    _needs_checkpointed_workflow,
    chat,
)

PRINCIPAL = HttpPrincipal(
    subject_id="test-client",
    traveler_id="trv_meridian_demo",
    authentication="test",
)


def test_concierge_offers_recovery_without_teaching_instructions_or_starting_work(monkeypatch):
    from unittest.mock import AsyncMock
    production = AsyncMock(side_effect=AssertionError("No unrelated production search"))
    workflow = AsyncMock(side_effect=AssertionError("Opening chat does not authorize recovery"))
    monkeypatch.setattr("backend.routers.chat.production_search", production)
    monkeypatch.setattr("backend.routers.chat.orchestration_workflow", workflow)
    prompt = "My flight was canceled. Rework my Tokyo trip, then check availability."
    response = asyncio.run(chat(ChatRequest(
        message=prompt, phase=4, experience="concierge", conversation_id="current-chat",
    ), PRINCIPAL))
    assert response.recovery_request == prompt
    assert response.conversation_id == "current-chat"
    assert "hold" in response.message
    assert not any(word in response.message.lower() for word in ["workflow", "phase", "checkpoint"])
    production.assert_not_called()
    workflow.assert_not_called()


def test_concierge_review_request_reaches_the_workflow_without_resuming(monkeypatch):
    from unittest.mock import AsyncMock
    workflow = AsyncMock(return_value=([], [], "Shortlist ready for review.", "recovery-thread", "paused", False))
    monkeypatch.setattr("backend.routers.chat.orchestration_workflow", workflow)
    response = asyncio.run(chat(ChatRequest(
        message="Rework my trip and check availability", phase=5, review_only=True,
        conversation_id="recovery-thread", travelers_count=2,
    ), PRINCIPAL))
    assert response.workflow_status == "paused"
    assert workflow.call_args.kwargs["review_only"] is True
    assert workflow.call_args.kwargs["resume"] is False
    assert workflow.call_args.kwargs["travelers_count"] == 2


def test_review_only_cannot_resume_into_an_inventory_action(monkeypatch):
    from fastapi import HTTPException
    from unittest.mock import AsyncMock
    workflow = AsyncMock()
    monkeypatch.setattr("backend.routers.chat.orchestration_workflow", workflow)
    with pytest.raises(HTTPException) as error:
        asyncio.run(chat(ChatRequest(
            message="Resume workflow from checkpoint", phase=5, review_only=True,
            conversation_id="recovery-thread",
        ), PRINCIPAL))
    assert error.value.status_code == 422
    workflow.assert_not_called()


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
            ),
            PRINCIPAL,
        )
    )

    assert response.message == _PHASE4_WORKFLOW_TRANSITION_MESSAGE
    assert response.conversation_id == "conv-demo"
    assert response.products
    assert [product.name for product in response.products] == [
        "Tokyo Indie Neighborhood Walk"
    ]
    assert any(a.title == "Saved-step workflow required" for a in response.activities)
    assert all("checkpoint" not in a.title.lower() for a in response.activities)


@pytest.mark.parametrize("message", [
    "Recall my October Tokyo plan and use my saved preferences to recommend the next step.",
    "What durations are available for Tokyo Culture & Cuisine?",
    "My flight was cancelled. Rework the trip, then check duration availability.",
])
def test_phase4_memory_off_stops_before_recall_or_writeback(monkeypatch, message) -> None:
    called = False

    async def unexpected_production_search(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("production_search must not run while memory is disabled")

    monkeypatch.setattr(
        "backend.routers.chat.production_search",
        unexpected_production_search,
    )

    monkeypatch.setattr("backend.routers.chat.retrieval_availability_search", unexpected_production_search)
    monkeypatch.setattr("backend.routers.chat._polish_and_record", unexpected_production_search)
    response = asyncio.run(
        chat(
            ChatRequest(
                phase=4,
                customer_id="trv_meridian_demo",
                memory_enabled=False,
                message=message,
            ),
            PRINCIPAL,
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


@pytest.mark.parametrize("message", [
    "Recall my Tokyo plan and recommend the next step.",
    "What durations are available for Tokyo Culture & Cuisine?",
])
def test_phase4_returns_managed_runtime_decision_without_local_rewrite(
    monkeypatch, message,
) -> None:
    runtime_message = "Managed Runtime selected Tokyo Culture using Jordan's saved context."

    async def fake_production_search(*args, **kwargs):
        return (
            [
                Product(
                    product_id="CTY-002",
                    name="Tokyo Culture & Cuisine",
                    brand="Meridian Partner",
                    price=2499.0,
                    description="Tokyo culture trip",
                    image_url="",
                    category="City Breaks",
                    similarity=0.71,
                )
            ],
            [],
            runtime_message,
            "conv-runtime",
            [],
        )

    async def unexpected_polish(*args, **kwargs):
        raise AssertionError("Production must not rewrite the managed Runtime decision")

    monkeypatch.setattr(
        "backend.routers.chat.production_search",
        fake_production_search,
    )
    monkeypatch.setattr(
        "backend.routers.chat._polish_phase_reply",
        unexpected_polish,
    )

    response = asyncio.run(
        chat(
            ChatRequest(
                phase=4,
                customer_id="trv_meridian_demo",
                message=message,
            ),
            PRINCIPAL,
        )
    )

    assert response.message == runtime_message
    assert response.conversation_id == "conv-runtime"


def test_workflow_receipt_is_not_rewritten_by_a_prose_model(monkeypatch):
    from unittest.mock import AsyncMock
    saved = "Aurora recorded courtesy hold hold-one. Flight seats have not been reserved."
    monkeypatch.setattr("backend.routers.chat.orchestration_workflow", AsyncMock(
        return_value=([], [], saved, "saved-thread", "resumed", True)))
    monkeypatch.setattr("backend.routers.chat._load_workflow_memory_facts", AsyncMock(return_value=[]))
    polish = AsyncMock(side_effect=AssertionError("A receipt must not be rewritten"))
    monkeypatch.setattr("backend.routers.chat._polish_and_record", polish)
    response = asyncio.run(chat(ChatRequest(phase=5, message="Resume workflow from checkpoint",
                                             conversation_id="saved-thread"), PRINCIPAL))
    assert response.message == saved
    polish.assert_not_awaited()
