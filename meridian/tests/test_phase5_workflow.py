"""
Phase 5 LangGraph workflow regression tests.

These do NOT touch Aurora — they stub the search / availability functions
so the StateGraph compiles and routes correctly with `MemorySaver`.  The
goal is to catch import / wiring breakage in CI without needing AWS creds.
"""

from __future__ import annotations

import asyncio
from typing import Any, List, Tuple

import pytest

from backend.agents.phase5.workflow import (
    Phase5Workflow,
    _classify_intent,
)


def test_classify_routes_search_query() -> None:
    assert _classify_intent("Find me a Kyoto cultural trip") == "search"


def test_classify_routes_availability_query() -> None:
    assert _classify_intent("What dates are available for Tokyo?") == "availability"
    assert _classify_intent("When can I depart for Lisbon?") == "availability"


def test_classify_routes_memory_query() -> None:
    assert _classify_intent("Do you remember our last trip?") == "memory_recall"


def _build_workflow() -> Phase5Workflow:
    async def fake_search(q: str, limit: int = 5) -> Tuple[List[Any], List[Any]]:
        return ([{"package_id": "pkg-a", "name": "Kyoto cultural"}], [])

    async def fake_avail(q: str) -> Tuple[List[Any], List[Any], str]:
        return ([{"package_id": "pkg-a", "date": "2026-10-12"}], [], "")

    return Phase5Workflow(search_fn=fake_search, availability_fn=fake_avail)


def test_workflow_search_branch() -> None:
    wf = _build_workflow()
    res = asyncio.run(
        wf.run("Find me a Kyoto cultural trip", traveler_id="t1", conversation_id="c1")
    )
    assert res["intent"] == "search"
    assert len(res["packages"]) == 1
    titles = [a.get("title", "") for a in res.get("activities", [])]
    assert any("classify → search" in t for t in titles)
    assert any("synthesize" in t for t in titles)


def test_workflow_availability_branch() -> None:
    wf = _build_workflow()
    res = asyncio.run(
        wf.run("What dates are available for Tokyo?", traveler_id="t1", conversation_id="c2")
    )
    assert res["intent"] == "availability"
    titles = [a.get("title", "") for a in res.get("activities", [])]
    assert any("classify → availability" in t for t in titles)


def test_checkpointer_kind_is_memory_when_dsn_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGGRAPH_CHECKPOINT_DSN", raising=False)
    wf = _build_workflow()
    assert "MemorySaver" in wf.checkpointer_kind
