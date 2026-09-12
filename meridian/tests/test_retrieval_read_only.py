"""Retrieval must not turn a model-selected action into a booking write."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.agents.retrieval_03 import booking_agent, supervisor
from backend.agents.sql_01 import agent as sql_agent


def _without_model_or_database(monkeypatch, module, db):
    monkeypatch.setattr(module, "get_rds_data_client", lambda: db)
    monkeypatch.setattr(module, "BedrockModel", lambda **kwargs: None)
    monkeypatch.setattr(
        module, "Agent", lambda **kwargs: SimpleNamespace(**kwargs)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["process", "confirm", "hold"])
async def test_model_selected_write_cannot_reach_booking_specialist(action):
    # Even if a specialist accidentally regains a writer, the supervisor must
    # refuse the old delegation before making any specialist call.
    specialist = SimpleNamespace(
        process_booking=AsyncMock(),
        calculate_booking_total=AsyncMock(),
    )
    agent = object.__new__(supervisor.RetrievalAgent)
    agent.booking_agent = specialist
    agent.activity_callback = lambda entry: None

    result = await agent._delegate_to_booking(
        action, items=[{"package_id": "PKG-1", "travelers_count": 2}]
    )

    assert result["error"] == "governed_booking_required"
    assert "booking_id" not in result
    specialist.process_booking.assert_not_awaited()
    specialist.calculate_booking_total.assert_not_awaited()


@pytest.mark.asyncio
async def test_pricing_delegation_still_returns_a_read_only_estimate(monkeypatch):
    db = SimpleNamespace(
        execute_one=AsyncMock(
            return_value={
                "package_id": "PKG-1",
                "name": "Sample trip",
                "price_per_person": Decimal("100"),
            }
        ),
        execute=AsyncMock(side_effect=AssertionError("No write is permitted")),
    )
    _without_model_or_database(monkeypatch, booking_agent, db)
    specialist = booking_agent.BookingAgent()
    agent = object.__new__(supervisor.RetrievalAgent)
    agent.booking_agent = specialist
    agent.activity_callback = lambda entry: None

    result = await agent._delegate_to_booking(
        "calculate", items=[{"package_id": "PKG-1", "travelers_count": 2}]
    )

    assert result["subtotal"] == 200
    assert "booking_id" not in result
    assert "status" not in result
    db.execute.assert_not_awaited()
    query, parameters = db.execute_one.await_args.args
    assert query.lstrip().startswith("SELECT ")
    assert parameters == ("PKG-1",)


@pytest.mark.parametrize(
    ("module", "agent_type", "permitted_tools"),
    [
        (
            booking_agent,
            booking_agent.BookingAgent,
            {"_calculate_booking_total_tool"},
        ),
        (
            sql_agent,
            sql_agent.SQLAgent,
            {
                "_lookup_trip_package",
                "_search_trip_packages",
                "_check_departure_availability",
                "_calculate_booking_total",
            },
        ),
    ],
)
def test_catalog_agents_expose_only_read_tools(
    monkeypatch, module, agent_type, permitted_tools
):
    _without_model_or_database(monkeypatch, module, SimpleNamespace())
    agent = agent_type()

    assert {tool.__name__ for tool in agent.agent.tools} == permitted_tools
    assert not hasattr(agent, "process_booking")
    assert not hasattr(agent, "_process_booking")
