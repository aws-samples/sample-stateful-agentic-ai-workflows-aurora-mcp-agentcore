"""The model badge must name whichever model actually wrote a reply,
including a fallback model, and must be absent for a pure tool result.

Before this change, the only badge-shaping value the frontend read was
`/api/health`'s `bedrock_model_label`, computed once from the configured
primary - so a turn the Haiku or Opus fallback actually wrote still showed
the primary's name. `ChatResponse.model_label` now carries the truth per
turn.
"""

import asyncio

import pytest

import backend.routers.chat as chat_router
from backend.llm_polish import PolishResult
from backend.routers.chat import ChatRequest, _polish_and_record, chat
from backend.http_auth import HttpPrincipal

PRINCIPAL = HttpPrincipal(
    subject_id="test-client",
    traveler_id="trv_meridian_demo",
    authentication="test",
)


async def _fake_polish(_query: str, _facts: str) -> PolishResult:
    return PolishResult(
        text="Polished reply.",
        model_id="global.anthropic.claude-haiku-4-5-20251001-v1:0",
        note=None,
    )


async def _fake_polish_failed(_query: str, _facts: str) -> PolishResult:
    return PolishResult(text="", model_id=None, note="every model in the chain failed")


def test_polish_and_record_names_the_fallback_model_that_actually_wrote_the_reply(monkeypatch):
    monkeypatch.setattr(chat_router, "polish_concierge_reply", _fake_polish)
    activities: list = []

    message, model_label = asyncio.run(
        _polish_and_record(
            phase=3,
            mode_label="Retrieval",
            agent_name="RetrievalAgent",
            user_query="Find a quiet retreat",
            raw_message="Here are 2 trips.",
            products=[],
            activities=activities,
        )
    )

    assert message == "Polished reply."
    # The configured primary is Sonnet 5; the fake fallback answered with
    # Haiku instead, and the label must say so, not the primary's name.
    assert model_label == "Claude Haiku 4.5"
    assert model_label != "Claude Sonnet 5"


def test_polish_and_record_reports_no_model_when_every_model_fails(monkeypatch):
    monkeypatch.setattr(chat_router, "polish_concierge_reply", _fake_polish_failed)
    activities: list = []

    message, model_label = asyncio.run(
        _polish_and_record(
            phase=3,
            mode_label="Retrieval",
            agent_name="RetrievalAgent",
            user_query="Find a quiet retreat",
            raw_message="Here are 2 trips.",
            products=[],
            activities=activities,
        )
    )

    assert message == "Here are 2 trips."
    assert model_label is None


@pytest.mark.database
def test_phase_3_reply_carries_the_model_that_wrote_it(monkeypatch):
    monkeypatch.setattr(chat_router, "polish_concierge_reply", _fake_polish)

    response = asyncio.run(
        chat(
            ChatRequest(
                phase=3,
                message="Find a quiet, romantic wine-country retreat with a private villa.",
                customer_id="trv_meridian_demo",
            ),
            PRINCIPAL,
        )
    )

    if response.products:
        assert response.model_label == "Claude Haiku 4.5"
    else:
        # A zero-result retrieval turn never calls polish - still a real,
        # honest state, just not the one this test is targeting.
        assert response.model_label is None


@pytest.mark.database
def test_pure_tool_result_reply_carries_no_model_badge():
    """Phase 2's domain-tool replies never touch Bedrock polish."""
    response = asyncio.run(
        chat(
            ChatRequest(
                phase=2,
                message="What is the price range for Tokyo trips?",
                customer_id="trv_meridian_demo",
            ),
            PRINCIPAL,
        )
    )

    assert response.model_label is None
