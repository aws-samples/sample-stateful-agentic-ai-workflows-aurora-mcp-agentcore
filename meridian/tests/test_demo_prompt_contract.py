"""Contract tests for the five-phase presenter prompt ladder."""

import asyncio
from contextlib import asynccontextmanager

import backend.routers.chat as chat_router
from backend.routers.chat import (
    MemoryFact,
    _append_recovery_memory_receipt,
    _call_domain_tool,
    _format_domain_reply,
    _is_availability_query,
    _is_memory_recall_query,
    _is_semantic_intent_query,
    _needs_checkpointed_workflow,
    _wants_domain_tool,
    is_availability_query,
)
from scripts.travel_catalog import (
    DEMO_TRAVELER_ID,
    TRAVELERS,
    TRAVELER_PREFERENCES,
)


COMPARE_AND_FX = (
    "Compare three trip types side by side and convert their prices to euros."
)
MCP_SEASONAL = (
    "What is the off-season price range for Tokyo trips in November?"
)
RETRIEVAL_INTENT = (
    "I want a quiet, romantic escape in wine country, ideally with a villa."
)
TUSCANY_AVAILABILITY = (
    "Which trip lengths are still available for Tuscany Wine & Wellness?"
)
MEMORY_RECALL = (
    "Recall my October Tokyo plan and use my saved preferences to recommend "
    "the next step."
)
WORKFLOW_PLAN = (
    "My JFK-to-Tokyo flight was cancelled. Rework the trip, then check "
    "duration availability for the best three options."
)
WORKFLOW_PLAN_KYOTO = (
    "Plan the Kyoto extension: find matching packages, then verify available "
    "duration options."
)


def test_demo_traveler_airport_contract() -> None:
    traveler = next(t for t in TRAVELERS if t["traveler_id"] == DEMO_TRAVELER_ID)
    preferences = {
        pref["preference_key"]: pref["preference_value"]
        for pref in TRAVELER_PREFERENCES
        if pref["preference_type"] == "logistics"
    }

    assert traveler["home_airport"] == "JFK"
    assert preferences["home_airport"] == "JFK"
    assert preferences["avoid_connections"] == "LHR, EWR"
    assert "JFK" not in {
        airport.strip()
        for airport in preferences["avoid_connections"].split(",")
    }


def test_mcp_prompts_select_domain_tools() -> None:
    assert _wants_domain_tool(COMPARE_AND_FX)
    assert _wants_domain_tool(MCP_SEASONAL)
    assert not _wants_domain_tool(RETRIEVAL_INTENT)
    assert _is_semantic_intent_query(RETRIEVAL_INTENT)
    assert not _is_semantic_intent_query(COMPARE_AND_FX)


def test_early_phase_stretch_prompts_return_explicit_boundaries() -> None:
    sql_response = asyncio.run(
        chat_router.chat(
            chat_router.ChatRequest(
                phase=1,
                message=COMPARE_AND_FX,
                customer_id="trv_meridian_demo",
            )
        )
    )
    assert "Switch to MCP" in sql_response.message
    assert not sql_response.products

    mcp_response = asyncio.run(
        chat_router.chat(
            chat_router.ChatRequest(
                phase=2,
                message=RETRIEVAL_INTENT,
                customer_id="trv_meridian_demo",
            )
        )
    )
    assert "Switch to Retrieval" in mcp_response.message
    assert not mcp_response.products


def test_retrieval_memory_stretch_is_concise_and_honest() -> None:
    response = asyncio.run(
        chat_router.chat(
            chat_router.ChatRequest(
                phase=3,
                message=MEMORY_RECALL,
                customer_id="trv_meridian_demo",
            )
        )
    )
    assert "no traveler profile or prior-turn memory" in response.message
    assert "Switch to Production" in response.message
    assert not response.products


def test_retrieval_prompts_select_expected_special_paths() -> None:
    assert _is_availability_query(TUSCANY_AVAILABILITY)
    assert is_availability_query(TUSCANY_AVAILABILITY)
    assert _is_memory_recall_query(MEMORY_RECALL)


def test_production_stretch_requires_checkpointed_workflow() -> None:
    assert _needs_checkpointed_workflow(WORKFLOW_PLAN)
    assert _needs_checkpointed_workflow(WORKFLOW_PLAN_KYOTO)
    assert not _needs_checkpointed_workflow(TUSCANY_AVAILABILITY)


def test_workflow_recovery_surfaces_applied_aurora_memory() -> None:
    message = _append_recovery_memory_receipt(
        "I found two viable recovery options.",
        WORKFLOW_PLAN,
        [
            MemoryFact(
                key="home_airport",
                value="JFK",
                source="profile",
                confidence=1.0,
            ),
            MemoryFact(
                key="shellfish_allergy",
                value="Exclude shellfish",
                source="support_ticket",
                confidence=1.0,
            ),
        ],
    )

    assert "Traveler context applied from Aurora" in message
    assert "**Home airport:** JFK" in message
    assert "shellfish allergy" in message


def test_multi_price_fx_reply_names_every_compared_package() -> None:
    reply = _format_domain_reply(
        "currency_convert",
        {
            "to": "EUR",
            "conversions": [
                {
                    "name": "Tuscany Wine & Wellness",
                    "amount": 3699.0,
                    "from": "USD",
                    "converted": 3403.08,
                    "to": "EUR",
                },
                {
                    "name": "Kyoto Ryokan & Onsen",
                    "amount": 3299.0,
                    "from": "USD",
                    "converted": 3035.08,
                    "to": "EUR",
                },
            ],
        },
    )

    assert "Tuscany Wine & Wellness" in reply
    assert "Kyoto Ryokan & Onsen" in reply
    assert "Indicative rates" in reply


def test_compare_prompt_converts_each_package_price(monkeypatch) -> None:
    packages = [
        {"package_id": "A", "name": "Trip A", "price_per_person": 1200.0},
        {"package_id": "B", "name": "Trip B", "price_per_person": 2200.0},
        {"package_id": "C", "name": "Trip C", "price_per_person": 3200.0},
    ]

    class FakeDb:
        async def execute(self, _sql):
            return [{"package_id": item["package_id"]} for item in packages]

    class FakeMcp:
        def __init__(self):
            self.calls = []

        async def call(self, tool, args):
            self.calls.append((tool, args))
            if tool == "compare_packages":
                return packages
            amount = args["amount"]
            return {
                "from": "USD",
                "to": args["to_ccy"],
                "amount": amount,
                "converted": round(amount * 0.92, 2),
                "rate": 0.92,
            }

    client = FakeMcp()

    @asynccontextmanager
    async def fake_session():
        yield client

    monkeypatch.setattr(chat_router, "get_rds_data_client", lambda: FakeDb())
    monkeypatch.setattr(chat_router, "concierge_mcp_session", fake_session)

    result = asyncio.run(_call_domain_tool(COMPARE_AND_FX))

    assert result["tool"] == "multi"
    currency_calls = [args for tool, args in client.calls if tool == "currency_convert"]
    assert [call["amount"] for call in currency_calls] == [1200.0, 2200.0, 3200.0]
