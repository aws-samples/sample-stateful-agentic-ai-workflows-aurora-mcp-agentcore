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
from langgraph.checkpoint.memory import MemorySaver

import backend.agents.orchestration_05.workflow as workflow_mod
from backend.agents.orchestration_05.workflow import (
    OrchestrationAgent,
    _classify_intent,
    _resolve_checkpoint_dsn,
)


@pytest.fixture(autouse=True)
def _disable_auto_checkpoint_dsn(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LANGGRAPH_CHECKPOINT_DSN", raising=False)
    monkeypatch.setenv("LANGGRAPH_AUTO_CHECKPOINT_DSN", "false")
    monkeypatch.setenv("LANGGRAPH_CHECKPOINT_REQUIRED", "false")
    monkeypatch.setenv("LANGGRAPH_CHECKPOINT_POOL_TIMEOUT", "10")
    monkeypatch.delenv("LANGGRAPH_DEMO_INTERRUPT_AFTER", raising=False)
    workflow_mod._checkpoint_backend = None
    workflow_mod._checkpoint_init_lock = None
    yield
    workflow_mod._checkpoint_backend = None
    workflow_mod._checkpoint_init_lock = None


def test_classify_routes_search_query() -> None:
    assert _classify_intent("Find me a Kyoto cultural trip") == "search"


def test_classify_routes_availability_query() -> None:
    assert _classify_intent("What dates are available for Tokyo?") == "availability"
    assert _classify_intent("When can I depart for Lisbon?") == "availability"
    assert (
        _classify_intent(
            "Which duration options are available for Amalfi Coast Villa Week?"
        )
        == "availability"
    )


def test_classify_routes_memory_query() -> None:
    assert _classify_intent("Do you remember our last trip?") == "memory_recall"
    assert (
        _classify_intent(
            "Using what we decided about my October Tokyo trip last time, "
            "what should I do next?"
        )
        == "memory_recall"
    )


def test_classify_routes_canonical_plan_query() -> None:
    assert (
        _classify_intent(
            "Plan the Kyoto extension: find matching packages, then verify "
            "available duration options."
        )
        == "plan"
    )


def _build_workflow() -> OrchestrationAgent:
    async def fake_search(q: str, limit: int = 5) -> Tuple[List[Any], List[Any]]:
        return ([{"package_id": "pkg-a", "name": "Kyoto cultural"}], [])

    async def fake_avail(q: str) -> Tuple[List[Any], List[Any], str]:
        return ([{"package_id": "pkg-a", "date": "2026-10-12"}], [], "")

    return OrchestrationAgent(search_fn=fake_search, availability_fn=fake_avail)


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
    wf = _build_workflow()
    asyncio.run(
        wf.run("Find me a Kyoto cultural trip", traveler_id="t1", conversation_id="c-memory")
    )
    assert "MemorySaver" in wf.checkpointer_kind


def test_checkpoint_dsn_can_be_built_from_aurora_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGGRAPH_CHECKPOINT_DSN", raising=False)
    monkeypatch.setenv("LANGGRAPH_AUTO_CHECKPOINT_DSN", "true")
    monkeypatch.setenv("AURORA_USERNAME", "demo user")
    monkeypatch.setenv("AURORA_PASSWORD", "p@ss word")
    monkeypatch.setenv("AURORA_HOST", "db.example.com")
    monkeypatch.setenv("AURORA_PORT", "5432")
    monkeypatch.setenv("AURORA_DATABASE", "meridian")
    monkeypatch.delenv("AURORA_SECRET_ARN", raising=False)

    assert (
        _resolve_checkpoint_dsn()
        == "postgresql://demo%20user:p%40ss%20word@db.example.com:5432/meridian?sslmode=require"
    )


def test_workflow_enters_async_postgres_saver_when_dsn_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: List[str] = []

    class FakeAsyncSaver(MemorySaver):
        def __init__(self, pool: Any):
            super().__init__()
            events.append(f"saver:{pool.name}")

        async def setup(self) -> None:
            events.append("setup")

    class FakePool:
        def __init__(self, *, conninfo: str, name: str, **_kwargs: Any):
            self.name = name
            events.append(f"pool:{conninfo}")

        async def open(self, *, wait: bool, timeout: float) -> None:
            events.append(f"open:{wait}:{int(timeout)}")

        async def close(self) -> None:
            events.append("close")

    monkeypatch.setenv("LANGGRAPH_CHECKPOINT_DSN", "postgresql://example")
    monkeypatch.setattr(workflow_mod, "AsyncPostgresSaver", FakeAsyncSaver)
    monkeypatch.setattr(workflow_mod, "AsyncConnectionPool", FakePool)
    monkeypatch.setattr(workflow_mod, "dict_row", object())

    wf = _build_workflow()
    assert wf.checkpointer_kind == "MemorySaver (initializing)"

    res = asyncio.run(
        wf.run(
            "What dates are available for Tokyo?",
            traveler_id="t1",
            conversation_id="c-postgres",
        )
    )

    assert res["intent"] == "availability"
    assert wf.checkpointer_kind == "PostgresSaver (Aurora · pooled)"
    assert events == [
        "pool:postgresql://example",
        "open:True:10",
        "saver:meridian-langgraph-checkpoints",
        "setup",
    ]
    titles = [a.get("title", "") for a in res.get("activities", [])]
    assert "Checkpoint · PostgresSaver.put" in titles

    asyncio.run(workflow_mod.close_checkpoint_backend())
    assert events[-1] == "close"


def test_required_checkpoint_store_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_CHECKPOINT_REQUIRED", "true")
    wf = _build_workflow()

    with pytest.raises(RuntimeError, match="Durable workflow checkpoints are required"):
        asyncio.run(
            wf.run(
                "Find me a Kyoto cultural trip",
                traveler_id="t1",
                conversation_id="c-required",
            )
        )


def test_workflow_can_pause_and_resume_same_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_DEMO_INTERRUPT_AFTER", "search")
    calls = {"search": 0, "availability": 0}

    async def scenario() -> tuple[dict, dict]:
        async def fake_search(q: str, limit: int = 5):
            calls["search"] += 1
            return ([{"package_id": "pkg-a", "name": "Tokyo replan"}], [])

        async def fake_avail(q: str):
            calls["availability"] += 1
            return ([{"package_id": "pkg-a", "date": "2026-10-12"}], [], "")

        wf = OrchestrationAgent(
            search_fn=fake_search,
            availability_fn=fake_avail,
        )
        paused = await wf.run(
            "Plan a Tokyo trip and check available departures",
            traveler_id="t1",
            conversation_id="c-resume",
        )
        resumed = await wf.run(
            "Resume workflow from checkpoint",
            traveler_id="t1",
            conversation_id="c-resume",
            resume=True,
        )
        return paused, resumed

    paused, resumed = asyncio.run(scenario())

    assert paused["workflow_status"] == "paused"
    assert resumed["workflow_status"] == "resumed"
    assert calls == {"search": 1, "availability": 1}
    titles = [a.get("title", "") for a in resumed.get("activities", [])]
    assert "Workflow resumed from checkpoint" in titles
