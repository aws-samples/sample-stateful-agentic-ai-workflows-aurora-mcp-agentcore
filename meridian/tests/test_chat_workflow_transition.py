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


def test_tokyo_plan_query_bridges_to_workflow() -> None:
    query = (
        "Plan our October Tokyo trip — find open dates, pick a Marriott "
        "property, and stage a Kyoto side trip."
    )

    assert _needs_checkpointed_workflow(query)
    assert (
        _PHASE4_WORKFLOW_TRANSITION_MESSAGE
        == "I can recall your Tokyo context and find candidate trips, but this "
        "request has multiple dependent steps: shortlist Tokyo, verify October "
        "availability, choose a Bonvoy-aligned stay, and stage Kyoto. I need a "
        "workflow to checkpoint each step before committing the plan."
    )


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
                    "Plan our October Tokyo trip — find open dates, pick a "
                    "Marriott property, and stage a Kyoto side trip."
                ),
            )
        )
    )

    assert response.message == _PHASE4_WORKFLOW_TRANSITION_MESSAGE
    assert response.conversation_id == "conv-demo"
    assert response.products
    assert any(a.title == "Checkpointed workflow required" for a in response.activities)
