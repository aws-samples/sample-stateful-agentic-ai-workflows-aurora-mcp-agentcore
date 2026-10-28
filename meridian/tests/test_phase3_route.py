"""Phase 3 answers with the agent's own reply and keeps its recall boundary."""

import logging
from unittest.mock import AsyncMock

import pytest

import backend.routers.chat as chat_router
from backend.activity import Product, create_activity
from backend.agents.phase_03_retrieval import RetrievalTurn, RetrievalTurnError
from backend.http_auth import HttpPrincipal

PRINCIPAL = HttpPrincipal(
    subject_id="test-client", traveler_id="trv_meridian_demo", authentication="test")
SECRET = "hunter2"
ARN_SECRET = f"arn:aws:rds:us-east-1:123456789012:cluster:c password={SECRET}"
EM_DASH = "\u2014"
MODEL = "global.anthropic.claude-sonnet-5-5"


def turn(message=f"Tokyo Culture fits {EM_DASH} $2,499 per traveler.", products=True):
    return RetrievalTurn(
        message=message,
        products=[Product(product_id="CTY-002", name="Tokyo Culture", brand="Nippon", price=2499.0,
                          description="d", image_url="", category="City Breaks",
                          similarity=0.9)] if products else [],
        activities=[create_activity("reasoning", title="Retrieval agent: turn started")],
        tools_called=["hybrid_trip_search"], model_id=MODEL)


async def ask(message, **request):
    return await chat_router.chat(
        chat_router.ChatRequest(message=message, phase=3, **request), PRINCIPAL)


@pytest.mark.asyncio
async def test_the_visible_reply_is_the_agents_with_voice_punctuation_only(monkeypatch):
    monkeypatch.setattr(chat_router, "run_retrieval_turn", AsyncMock(return_value=turn()))

    response = await ask("Find a quiet Tokyo retreat")

    assert response.message == "Tokyo Culture fits, $2,499 per traveler."
    assert [p.product_id for p in response.products] == ["CTY-002"]
    assert response.model_label == "Claude Sonnet 5.5"
    assert any(a.title == "Retrieval agent: turn started" for a in response.activities)


@pytest.mark.asyncio
async def test_the_authorized_traveler_is_what_the_agent_pins(monkeypatch):
    run = AsyncMock(return_value=turn())
    monkeypatch.setattr(chat_router, "run_retrieval_turn", run)

    await ask("How many Bonvoy points do I have?", customer_id="me")

    assert run.await_args.kwargs["traveler_id"] == "trv_meridian_demo"


@pytest.mark.asyncio
async def test_an_availability_question_reaches_the_agent_not_a_keyword_shortcut(monkeypatch):
    run = AsyncMock(return_value=turn("CTY-002 has 4 places left.", products=False))
    monkeypatch.setattr(chat_router, "run_retrieval_turn", run)

    response = await ask("Is TKY-003 available in June?")

    run.assert_awaited_once()
    assert response.message == "CTY-002 has 4 places left."
    assert not response.products


@pytest.mark.asyncio
async def test_the_recall_boundary_stays_deterministic_and_runs_no_model(monkeypatch):
    run = AsyncMock(side_effect=AssertionError("Phase 3 has no memory; no model runs"))
    monkeypatch.setattr(chat_router, "run_retrieval_turn", run)

    response = await ask("What did we discuss last time?")

    run.assert_not_called()
    assert "Switch to Production" in response.message
    assert "no traveler profile or prior-turn memory" in response.message
    assert not response.products
    assert any(a.title.startswith("Memory-recall prompt detected") for a in response.activities)


@pytest.mark.asyncio
async def test_a_failing_agent_does_not_leak_its_exception_text(monkeypatch, caplog):
    monkeypatch.setattr(chat_router, "run_retrieval_turn", AsyncMock(
        side_effect=RetrievalTurnError(ARN_SECRET)))

    with caplog.at_level(logging.ERROR):
        response = await ask("Find a quiet Tokyo retreat")

    text = response.message + " ".join(f"{a.title} {a.details}" for a in response.activities)
    assert SECRET not in text and "123456789012" not in text
    assert response.message == "I encountered an error. Please try again or browse featured trips."
    assert any(a.activity_type == "error" and "The request failed. Reference " in a.details
               for a in response.activities)


def test_nothing_rewrites_a_phase_three_reply_any_more():
    for name in ("polish_concierge_reply", "_polish_and_record", "_polish_phase_reply",
                 "retrieval_supervisor_search", "_is_availability_query",
                 "is_availability_query", "retrieval_availability_search"):
        assert not hasattr(chat_router, name), name


def test_the_supervisor_and_the_specialists_are_gone():
    from pathlib import Path

    folder = Path(chat_router.__file__).parents[1] / "agents" / "phase_03_retrieval"
    for name in ("supervisor", "search_agent", "package_agent", "booking_agent"):
        assert not (folder / f"{name}.py").exists(), name
    assert not (Path(chat_router.__file__).parents[1] / "llm_polish.py").exists()


class NoMcpPool:
    """A pool with no MCP tools: the turn runs only its two in-process tools."""

    def acquire(self):
        return 1, []

    def release(self, _generation):
        return None

    def reset(self, _generation):
        return None


@pytest.mark.asyncio
async def test_a_reranked_row_reaches_the_returned_product_with_its_rerank_fields(monkeypatch):
    from backend.agents.phase_03_retrieval import agent as phase3
    from backend.tools import catalog_tools
    from tests.scripted_model import ScriptedModel

    reranked = {
        "package_id": "CTY-002", "name": "Tokyo Culture", "operator": "Nippon",
        "price_per_person": 2499.0, "description": "d", "image_url": "",
        "trip_type": "City Breaks", "destination": "Tokyo", "region": "Asia",
        "durations": ["7 nights"], "highlights": ["Sushi"], "similarity": 0.91,
        "pre_rerank_position": 3, "pre_rerank_similarity": 0.62, "rank_delta": 2,
    }

    async def search(_query, limit=5):
        return {"packages": [reranked], "candidates": {"semantic": 3, "lexical": 0, "merged": 3},
                "reranked": True}, []

    model = ScriptedModel([
        {"kind": "tool", "id": "t1", "name": "hybrid_trip_search",
         "input": {"query": "Tokyo", "limit": 5}},
        {"kind": "text", "text": "Tokyo Culture fits."},
    ])
    real_run = phase3.run_retrieval_turn

    async def run_with_scripted_model(query, **kwargs):
        return await real_run(query, model=model, pool=NoMcpPool(), **kwargs)

    monkeypatch.setattr(catalog_tools, "hybrid_trip_search_with_spans", search)
    monkeypatch.setattr(chat_router, "run_retrieval_turn", run_with_scripted_model)

    response = await ask("Find a Tokyo trip")

    (product,) = response.products
    assert product.pre_rerank_position == 3
    assert product.pre_rerank_similarity == 0.62
    assert product.rank_delta == 2
