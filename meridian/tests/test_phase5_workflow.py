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
            "Recall my October Tokyo plan and use my saved preferences to "
            "recommend the next step."
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

    async def fake_avail(
        q: str,
        package_id: str | None = None,
    ) -> Tuple[List[Any], List[Any], str]:
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


@pytest.mark.parametrize(
    "present",
    [
        pytest.param({}, id="nothing-configured"),
        pytest.param(
            {
                "AURORA_USERNAME": "meridian_admin",
                "AURORA_HOST": "db.example.com",
                "AURORA_DATABASE": "meridian",
            },
            id="documented-default-no-password",
        ),
        pytest.param(
            {
                "AURORA_USERNAME": "meridian_admin",
                "AURORA_PASSWORD": "   ",
                "AURORA_HOST": "db.example.com",
                "AURORA_DATABASE": "meridian",
            },
            id="blank-password",
        ),
    ],
)
def test_checkpoint_dsn_degrades_instead_of_raising(
    monkeypatch: pytest.MonkeyPatch, present: dict
) -> None:
    """Missing checkpoint credentials must fall back, not crash Phase 5.

    ``.env.example`` deliberately leaves ``AURORA_PASSWORD`` unset and steers
    operators to the Data API, so the documented default configuration reaches
    this path on every Phase 5 turn.
    """
    for name in (
        "LANGGRAPH_CHECKPOINT_DSN",
        "LANGGRAPH_CHECKPOINT_USERNAME",
        "LANGGRAPH_CHECKPOINT_PASSWORD",
        "LANGGRAPH_CHECKPOINT_HOST",
        "LANGGRAPH_CHECKPOINT_PORT",
        "LANGGRAPH_CHECKPOINT_DATABASE",
        "AURORA_USERNAME",
        "AURORA_PASSWORD",
        "AURORA_HOST",
        "AURORA_CLUSTER_ENDPOINT",
        "AURORA_PORT",
        "AURORA_DATABASE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANGGRAPH_AUTO_CHECKPOINT_DSN", "true")
    for name, value in present.items():
        monkeypatch.setenv(name, value)

    assert _resolve_checkpoint_dsn() is None


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

        def connection(self, *, timeout: float):
            events.append(f"preflight:{int(timeout)}")

            class ConnectionContext:
                async def __aenter__(self):
                    class Connection:
                        async def execute(self, statement: str) -> None:
                            events.append(f"execute:{statement}")

                    return Connection()

                async def __aexit__(self, *_args: Any) -> None:
                    return None

            return ConnectionContext()

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
        "preflight:3",
        "execute:SELECT 1",
    ]
    titles = [a.get("title", "") for a in res.get("activities", [])]
    # The span names the backend that actually ran, not a fixed class name.
    assert "Checkpoint · PostgresSaver (Aurora · pooled).put" in titles
    assert wf.checkpointer_durable is True

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

        async def fake_avail(q: str, package_id: str | None = None):
            calls["availability"] += 1
            return (
                [
                    {
                        "product_id": package_id or "pkg-a",
                        "available_sizes": ["7 nights"],
                        "availability": {"7 nights": 4},
                    }
                ],
                [],
                "",
            )

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
    assert resumed["resumed_after_restart"] is False
    assert calls == {"search": 1, "availability": 1}
    titles = [a.get("title", "") for a in resumed.get("activities", [])]
    assert "Workflow resumed from checkpoint" in titles


@pytest.mark.parametrize("cancellation_spelling", ["canceled", "cancelled"])
def test_canonical_recovery_finale_pauses_without_hidden_env_toggle(
    cancellation_spelling: str,
) -> None:
    async def fake_search(q: str, limit: int = 5):
        return ([{"product_id": "tokyo-1", "name": "Tokyo option"}], [])

    async def fake_avail(q: str, package_id: str | None = None):
        return ([], [], "")

    workflow = OrchestrationAgent(
        search_fn=fake_search,
        availability_fn=fake_avail,
    )
    result = asyncio.run(
        workflow.run(
            f"My JFK-to-Tokyo flight was {cancellation_spelling}. Rework the "
            "trip, then check duration availability for the best three options.",
            traveler_id="t1",
            conversation_id="c-canonical-recovery",
        )
    )

    assert result["workflow_status"] == "paused"
    assert result["packages"][0]["product_id"] == "tokyo-1"
    assert "Resume thread c-canonical-recovery" in result["response"]


def test_resume_detects_changed_worker_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_DEMO_INTERRUPT_AFTER", "search")

    async def scenario() -> dict:
        async def fake_search(q: str, limit: int = 5):
            return ([{"product_id": "tokyo-1", "name": "Tokyo option"}], [])

        async def fake_avail(q: str, package_id: str | None = None):
            return (
                [
                    {
                        "product_id": package_id or "tokyo-1",
                        "available_sizes": ["3 nights"],
                        "availability": {"3 nights": 2},
                    }
                ],
                [],
                "",
            )

        workflow = OrchestrationAgent(
            search_fn=fake_search,
            availability_fn=fake_avail,
        )
        await workflow.run(
            "Plan a Tokyo trip and check duration availability",
            traveler_id="t1",
            conversation_id="c-restarted",
        )
        monkeypatch.setattr(
            workflow_mod,
            "WORKER_INSTANCE_ID",
            "worker-after-restart",
        )
        return await workflow.run(
            "Resume workflow from checkpoint",
            traveler_id="t1",
            conversation_id="c-restarted",
            resume=True,
        )

    resumed = asyncio.run(scenario())

    assert resumed["workflow_status"] == "resumed"
    assert resumed["resumed_after_restart"] is True
    resumed_activity = next(
        activity
        for activity in resumed["activities"]
        if activity["title"] == "Workflow resumed from checkpoint"
    )
    fields = resumed_activity["telemetry"]["fields"]
    assert {"label": "worker_restart", "value": "observed"} in fields


def test_plan_fans_out_availability_and_merges_inventory() -> None:
    checked: List[str] = []

    async def fake_search(q: str, limit: int = 5):
        return (
            [
                {"product_id": f"pkg-{idx}", "name": f"Tokyo option {idx}"}
                for idx in range(1, 5)
            ],
            [],
        )

    async def fake_avail(q: str, package_id: str | None = None):
        assert package_id is not None
        checked.append(package_id)
        return (
            [
                {
                    "product_id": package_id,
                    "available_sizes": ["7 nights"],
                    "availability": {"7 nights": 3},
                }
            ],
            [],
            "",
        )

    workflow = OrchestrationAgent(
        search_fn=fake_search,
        availability_fn=fake_avail,
    )
    result = asyncio.run(
        workflow.run(
            "Plan a Tokyo trip and check duration availability",
            traveler_id="t1",
            conversation_id="c-fanout",
        )
    )

    assert checked == ["pkg-1", "pkg-2", "pkg-3"]
    assert result["availability_checks"] == 3
    assert len(result["packages"]) == 4
    assert result["packages"][0]["availability"] == {"7 nights": 3}
    assert "availability" not in result["packages"][3]
    titles = [a.get("title", "") for a in result.get("activities", [])]
    assert "Workflow node: availability fan-out" in titles
    details = [a.get("details", "") for a in result.get("activities", [])]
    assert any("duration inventory" in detail for detail in details)


# ---------------------------------------------------------------------------
# Courtesy hold: the workflow's only committed side effect.
# ---------------------------------------------------------------------------

# A disruption that does NOT match the canonical finale predicate, so the graph
# runs straight through to the hold instead of pausing after search.
WORKFLOW_PLAN = (
    "My flight was cancelled, rework the trip and show duration availability."
)


def _plan_workflow(hold_calls: List[dict]) -> OrchestrationAgent:
    """A plan-path workflow whose hold node records its Aurora call."""

    async def fake_search(q: str, limit: int = 5):
        return ([{"product_id": "TKY-003", "name": "Tokyo Executive Stopover",
                  "price": 1949}], [])

    async def fake_avail(q: str, package_id: str | None = None):
        return (
            [{"product_id": package_id or "TKY-003",
              "available_sizes": ["2 nights", "3 nights"],
              "availability": {"2 nights": 14, "3 nights": 0}}],
            [],
            "",
        )

    workflow = OrchestrationAgent(search_fn=fake_search, availability_fn=fake_avail)

    async def fake_hold(state):
        hold_calls.append(dict(state))
        return {"activities": list(state.get("activities", []))}

    workflow._node_hold = fake_hold  # type: ignore[method-assign]
    return workflow


def test_plan_path_places_a_courtesy_hold() -> None:
    """The recovery plan commits inventory, not just workflow position."""
    calls: List[dict] = []
    asyncio.run(
        _plan_workflow(calls).run(
            WORKFLOW_PLAN,
            traveler_id="trv_meridian_demo",
            conversation_id="hold-plan",
        )
    )
    assert len(calls) == 1, "plan path must reach the hold node exactly once"


def test_availability_lookup_never_holds_inventory() -> None:
    """A read must not reserve seats as a side effect."""
    calls: List[dict] = []
    asyncio.run(
        _plan_workflow(calls).run(
            "Which trip lengths are still available for Amalfi Coast Villa Week?",
            traveler_id="trv_meridian_demo",
            conversation_id="hold-availability",
        )
    )
    assert calls == [], "a bare availability intent must not place a hold"


def test_hold_picks_a_duration_that_has_inventory() -> None:
    """Never hold against a sold-out duration."""
    from backend.agents.orchestration_05.packages import (
        first_available_duration as _first_available_duration,
    )

    assert _first_available_duration(
        {"availability": {"2 nights": 0, "3 nights": 5}}
    ) == "3 nights"
    assert _first_available_duration(
        {"availability": {}, "available_sizes": ["6 nights"]}
    ) == "6 nights"


def test_failed_workflow_releases_a_committed_hold() -> None:
    """Compensation: seats go back when a later step fails."""
    released: List[str] = []

    async def fake_search(q: str, limit: int = 5):
        return ([{"product_id": "TKY-003", "name": "Tokyo", "price": 1949}], [])

    async def boom(q: str, package_id: str | None = None):
        raise RuntimeError("availability provider exploded")

    workflow = OrchestrationAgent(search_fn=fake_search, availability_fn=boom)

    scoped_to: List[object] = []

    async def record_release(state, *, expected_hold_id=None):
        # Compensation is now scoped to the run that failed, so the caller has
        # to say which hold it expects to be releasing.
        scoped_to.append(expected_hold_id)
        released.append(str(state.get("hold_id") or "no-hold"))

    workflow._release_hold = record_release  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="availability provider exploded"):
        asyncio.run(
            workflow.run(
                WORKFLOW_PLAN,
                traveler_id="trv_meridian_demo",
                conversation_id="hold-compensation",
            )
        )
    assert released, "a failing workflow must run the compensating release"
    assert scoped_to, "the release must be told which run's hold it is releasing"
