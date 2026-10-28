"""The model badge must name the model the Phase 3 agent actually ran on, and must be absent
for a pure tool result.

The only badge-shaping value the frontend read was `/api/health`'s `bedrock_model_label`,
computed once from the configured primary. `ChatResponse.model_label` carries the truth per
turn: the model id the agent reported.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

import backend.routers.chat as chat_router
from backend.activity import Product
from backend.agents.phase_03_retrieval import RetrievalTurn
from backend.http_auth import HttpPrincipal
from backend.routers.chat import ChatRequest, chat

PRINCIPAL = HttpPrincipal(
    subject_id="test-client",
    traveler_id="trv_meridian_demo",
    authentication="test",
)


def _turn(model_id):
    return RetrievalTurn(
        message="Two trips.", activities=[], tools_called=["hybrid_trip_search"],
        products=[Product(product_id="A", name="a", brand="b", price=1.0, description="",
                          image_url="", category="c")],
        model_id=model_id)


@pytest.mark.parametrize(("model_id", "label"), [
    ("global.anthropic.claude-sonnet-5-5", "Claude Sonnet 5.5"),
    ("global.anthropic.claude-haiku-4-5-20251001-v1:0", "Claude Haiku 4.5"),
])
def test_phase_3_reply_carries_the_model_the_agent_ran_on(monkeypatch, model_id, label):
    monkeypatch.setattr(chat_router, "run_retrieval_turn", AsyncMock(return_value=_turn(model_id)))

    response = asyncio.run(chat(ChatRequest(
        phase=3, message="Find a quiet retreat", customer_id="trv_meridian_demo"), PRINCIPAL))

    assert response.model_label == label


def test_the_recall_boundary_reply_carries_no_model_badge():
    response = asyncio.run(chat(ChatRequest(
        phase=3, message="What did we discuss last time?", customer_id="trv_meridian_demo"),
        PRINCIPAL))
    assert response.model_label is None


@pytest.mark.database
def test_pure_tool_result_reply_carries_no_model_badge():
    """Phase 2's domain-tool replies never touch a model."""
    response = asyncio.run(chat(
        ChatRequest(phase=2, message="What is the price range for Tokyo trips?",
                    customer_id="trv_meridian_demo"),
        PRINCIPAL))
    assert response.model_label is None
