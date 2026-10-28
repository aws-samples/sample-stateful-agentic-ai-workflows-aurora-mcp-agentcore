"""Phase 3 is one agent loop over cumulative tools, and nothing else writes its reply."""

import asyncio
import json
import logging
import re
import threading
import time
from pathlib import Path

import pytest
from botocore.exceptions import ClientError
from strands import tool
from strands.types.exceptions import MCPClientInitializationError

from backend.activity import create_activity
from backend.agents import concierge_definition as definition
from backend.agents.phase_03_retrieval import agent as phase3
from backend.agents.phase_03_retrieval import mcp_session
from backend.agents.phase_03_retrieval.tools import LOCAL_TOOLS
from backend.agents.strands_hooks import ToolSetupError
from backend.tools import catalog_tools, package_tools
from tests.scripted_model import ScriptedModel

HOOKS_LOGGER = "backend.agents.phase_03_retrieval.hooks"
TOOLS_LOGGER = "backend.agents.phase_03_retrieval.tools"

HYBRID_RESULT = {
    "packages": [{
        "package_id": "CTY-002", "name": "Tokyo Culture", "operator": "Nippon",
        "price_per_person": 2499.0, "description": "d", "image_url": "",
        "trip_type": "City Breaks", "destination": "Tokyo", "region": "Asia",
        "durations": ["7 nights"], "highlights": ["Sushi"], "similarity": 0.9,
    }],
    "candidates": {"semantic": 3, "lexical": 2, "merged": 4},
    "reranked": True,
}


class FakePool:
    """Hands out generation 7 and records how each turn gave it back."""

    def __init__(self, tools):
        self._tools, self.releases, self.resets, self.fail = tools, [], [], None

    def acquire(self):
        if self.fail:
            raise self.fail
        return 7, list(self._tools)

    def release(self, generation):
        self.releases.append(generation)

    def reset(self, generation):
        self.resets.append(generation)


def fake_mcp_tools(seen):
    @tool
    def search_trips(trip_type: str | None = None, max_price_per_person: float | None = None,
                     limit: int = 5) -> dict:
        """Filter trips.

        Args:
            trip_type: type.
            max_price_per_person: cap.
            limit: count.
        """
        return {"packages": [], "sql": "SELECT 1", "parameters": []}

    @tool
    def compare_packages(package_ids: list[str]) -> list:
        """Compare.

        Args:
            package_ids: ids.
        """
        return [{"package_id": "CTY-019", "name": "Kyoto", "price_per_person": 10.0}]

    @tool
    def price_range(destination: str) -> dict:
        """Price range.

        Args:
            destination: place.
        """
        return {"destination": destination, "low": 1.0, "average": 2.0, "high": 3.0}

    @tool
    def region_inventory(region: str, trip_type: str | None = None) -> dict:
        """Inventory.

        Args:
            region: region.
            trip_type: type.
        """
        return {"region": region}

    @tool
    def currency_convert(amount: float, from_ccy: str, to_ccy: str) -> dict:
        """Convert.

        Args:
            amount: amount.
            from_ccy: from.
            to_ccy: to.
        """
        return {"converted": amount}

    @tool
    def loyalty_balance(travelerId: str, program: str) -> dict:
        """Loyalty balance.

        Args:
            travelerId: traveler id.
            program: program.
        """
        seen.append(travelerId)
        return {"traveler_id": travelerId, "program": program, "points_balance": 86240,
                "tier": "Platinum Elite"}

    return [search_trips, compare_packages, price_range, region_inventory, currency_convert,
            loyalty_balance]


@pytest.fixture
def world(monkeypatch):
    seen = []

    async def hybrid(query, limit=5):
        spans = [create_activity("search", title="Hybrid candidates fetched",
                                 details="4 unique candidates (semantic=3, lexical=2)")]
        return HYBRID_RESULT, spans

    async def details(packageId):
        return {"package": {"package_id": packageId, "availability": {"7 nights": 4}},
                "summary": "Tokyo Culture, 7 nights: 4 places left"}

    monkeypatch.setattr(catalog_tools, "hybrid_trip_search_with_spans", hybrid)
    monkeypatch.setattr(package_tools, "get_package_details", details)
    pool = FakePool(fake_mcp_tools(seen))
    return pool, seen


def run(model, pool, query="Find me a Tokyo trip", **kwargs):
    return asyncio.run(phase3.run_retrieval_turn(
        query, traveler_id=kwargs.pop("traveler_id", "trv_pinned"), model=model, pool=pool,
        **kwargs))


def tool_turn(name, arguments, tool_id="t1"):
    return {"kind": "tool", "id": tool_id, "name": name, "input": arguments}


def test_the_agent_is_offered_exactly_the_phase_three_tools_and_prompt(world):
    pool, _ = world
    model = ScriptedModel([{"kind": "text", "text": "Hello."}])

    run(model, pool)

    assert sorted(model.calls[0]["tools"]) == sorted(definition.PHASE3_TOOLS)
    assert model.calls[0]["system"] == definition.system_prompt(3)
    assert not set(model.calls[0]["tools"]) & set(definition.WRITE_TOOLS)


def test_a_search_turn_returns_the_agents_own_reply_the_products_and_every_span(world):
    pool, _ = world
    model = ScriptedModel([
        tool_turn("hybrid_trip_search", {"query": "Tokyo", "limit": 5}),
        {"kind": "text", "text": "Tokyo Culture fits: $2,499 per traveler."},
    ])

    turn = run(model, pool)

    assert turn.message == "Tokyo Culture fits: $2,499 per traveler."
    assert [p.product_id for p in turn.products] == ["CTY-002"]
    assert turn.products[0].price == 2499.0 and turn.products[0].category == "City Breaks"
    assert turn.tools_called == ["hybrid_trip_search"]
    titles = [a.title for a in turn.activities]
    assert titles[0] == "Retrieval agent: turn started"
    assert titles.index("Tool call: hybrid_trip_search") < titles.index("Hybrid candidates fetched")
    assert titles.index("Hybrid candidates fetched") < titles.index(
        "Tool result: hybrid_trip_search")
    assert titles[-1] == "Retrieval agent: reply written"
    assert turn.model_id == "scripted"


def test_the_visible_reply_is_the_models_text_untouched(world):
    pool, _ = world
    reply = "Great! Tokyo Culture fits \u2014 $2,499 per traveler."
    model = ScriptedModel([{"kind": "text", "text": reply}])
    assert run(model, pool).message == reply


def test_products_are_limited_to_the_requested_count(world, monkeypatch):
    pool, _ = world
    many = {**HYBRID_RESULT, "packages": [
        {**HYBRID_RESULT["packages"][0], "package_id": f"P{i}"} for i in range(6)]}

    async def hybrid(query, limit=5):
        return many, []

    monkeypatch.setattr(catalog_tools, "hybrid_trip_search_with_spans", hybrid)
    model = ScriptedModel([tool_turn("hybrid_trip_search", {"query": "x"}),
                           {"kind": "text", "text": "Six."}])

    assert len(run(model, pool, limit=3).products) == 3


def test_an_availability_question_goes_to_the_agent_and_reads_package_details(world):
    pool, _ = world
    model = ScriptedModel([
        tool_turn("get_package_details", {"packageId": "CTY-002"}),
        {"kind": "text", "text": "CTY-002 has 4 places left on 7 nights."},
    ])

    turn = run(model, pool, query="Is CTY-002 available in June?")

    assert turn.tools_called == ["get_package_details"]
    assert turn.message.startswith("CTY-002 has 4 places")
    assert turn.products == []
    assert "Tool result: get_package_details" in [a.title for a in turn.activities]


def test_the_loyalty_tool_receives_the_pinned_traveler_not_the_models(world):
    pool, seen = world
    model = ScriptedModel([
        tool_turn("loyalty_balance", {"travelerId": "trv_decoy", "program": "Bonvoy"}),
        {"kind": "text", "text": "86,240 points, Platinum Elite."},
    ])

    run(model, pool, traveler_id="trv_pinned")

    assert seen == ["trv_pinned"]


def test_a_compare_result_shows_the_compared_trips(world):
    pool, _ = world
    model = ScriptedModel([
        tool_turn("compare_packages", {"package_ids": ["CTY-019"]}),
        {"kind": "text", "text": "Kyoto only."},
    ])
    assert [p.product_id for p in run(model, pool).products] == ["CTY-019"]


def test_a_tool_failure_is_traced_without_the_exception_text(world, monkeypatch):
    pool, _ = world

    async def failing(packageId):
        raise RuntimeError("password=hunter2")

    monkeypatch.setattr(package_tools, "get_package_details", failing)
    model = ScriptedModel([
        tool_turn("get_package_details", {"packageId": "CTY-002"}),
        {"kind": "text", "text": "I could not read that package."},
    ])

    turn = run(model, pool)

    failed = [a for a in turn.activities if a.title.startswith("Tool call failed")]
    assert failed and failed[0].activity_type == "error"
    assert "hunter2" not in json.dumps([a.model_dump() for a in turn.activities])
    assert turn.message == "I could not read that package." and pool.releases == [7]


def test_a_tool_that_could_not_be_set_up_ends_the_turn_with_an_error_not_a_reply(
    world, monkeypatch
):
    pool, _ = world

    async def denied(packageId):
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}},
                          "ExecuteStatement")

    monkeypatch.setattr(package_tools, "get_package_details", denied)
    model = ScriptedModel([
        tool_turn("get_package_details", {"packageId": "CTY-002"}),
        {"kind": "text", "text": "I could not read that package."},
    ])

    with pytest.raises(phase3.RetrievalTurnError, match="could not be set up") as raised:
        run(model, pool)

    assert isinstance(raised.value.__cause__, ToolSetupError)
    assert len(model.calls) == 1, "the stop hook ends the turn without another model call"
    assert pool.releases == [7] and pool.resets == [], "a local tool's error says nothing of MCP"


def test_the_setup_error_hook_and_the_traveler_pin_are_registered(world, monkeypatch):
    pool, _ = world
    captured = {}
    real = phase3.build_agent

    def spy(**kwargs):
        captured.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(phase3, "build_agent", spy)
    run(ScriptedModel([{"kind": "text", "text": "ok"}]), pool)

    names = {type(h).__name__ for h in captured["hooks"]}
    assert {"TravelerPinHook", "RetrievalTraceHooks", "StopOnToolSetupError"} <= names
    assert captured["phase"] == 3 and captured["callback_handler"] is None


def test_a_hung_turn_times_out_resets_the_client_and_says_so(world, monkeypatch):
    pool, _ = world

    class Hanging(ScriptedModel):
        async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
            await asyncio.sleep(30)
            yield {"messageStart": {"role": "assistant"}}

    monkeypatch.setattr(phase3, "TURN_TIMEOUT_SECONDS", 0.05)

    with pytest.raises(phase3.RetrievalTurnError, match="did not finish"):
        run(Hanging([]), pool)

    assert pool.resets == [7]


class Failing(ScriptedModel):
    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        raise RuntimeError("password=hunter2")
        yield


def test_a_model_failure_releases_the_client_and_names_no_internals(world):
    pool, _ = world

    with pytest.raises(phase3.RetrievalTurnError) as raised:
        run(Failing([]), pool)

    assert "hunter2" not in str(raised.value)
    assert pool.releases == [7] and pool.resets == [], "a Bedrock failure says nothing of MCP"


def replacing(tools, *replacements):
    """``tools`` with each replacement standing in for the tool of the same name."""
    names = {replacement.tool_name for replacement in replacements}
    return [t for t in tools if t.tool_name not in names] + list(replacements)


@tool(name="region_inventory")
def closed_session_region_inventory(region: str, trip_type: str | None = None) -> dict:
    """Inventory.

    Args:
        region: region.
        trip_type: type.
    """
    raise MCPClientInitializationError("the client session is not running")


@tool(name="price_range")
def failing_price_range(destination: str) -> dict:
    """Price range.

    Args:
        destination: place.
    """
    raise RuntimeError("wedged pipe in 123456789012")


def test_a_failed_mcp_tool_call_resets_the_client_even_when_the_turn_answers(world):
    _, seen = world
    pool = FakePool(replacing(fake_mcp_tools(seen), failing_price_range))
    model = ScriptedModel([tool_turn("price_range", {"destination": "Kyoto"}),
                           {"kind": "text", "text": "Prices are unavailable."}])

    assert run(model, pool).message == "Prices are unavailable."
    assert pool.resets == [7] and pool.releases == []


def test_a_closed_mcp_session_ends_the_turn_and_resets_the_client(world):
    _, seen = world
    pool = FakePool(replacing(fake_mcp_tools(seen), closed_session_region_inventory))
    model = ScriptedModel([tool_turn("region_inventory", {"region": "Asia"}),
                           {"kind": "text", "text": "unused"}])

    with pytest.raises(phase3.RetrievalTurnError, match="could not be set up"):
        run(model, pool)

    assert pool.resets == [7] and pool.releases == [] and len(model.calls) == 1


def test_a_failed_tool_call_logs_its_traceback_with_account_ids_masked(world, caplog):
    _, seen = world
    pool = FakePool(replacing(fake_mcp_tools(seen), failing_price_range))
    model = ScriptedModel([tool_turn("price_range", {"destination": "Kyoto"}),
                           {"kind": "text", "text": "Prices are unavailable."}])

    with caplog.at_level(logging.WARNING, logger=HOOKS_LOGGER):
        run(model, pool)

    [logged] = [record for record in caplog.records if record.name == HOOKS_LOGGER]
    assert not logged.exc_info, "the raw exception stays out of the record"
    assert "Traceback" in caplog.text and "failing_price_range" in caplog.text
    assert "RuntimeError" in caplog.text and "wedged pipe in <acct>" in caplog.text
    assert "123456789012" not in caplog.text


class RecordingModel(ScriptedModel):
    """Also keeps a copy of the messages the agent sent on every model call."""

    def __init__(self, turns):
        super().__init__(turns)
        self.messages = []

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.messages.append(json.loads(json.dumps(messages, default=str)))
        async for event in super().stream(messages, tool_specs, system_prompt, **kwargs):
            yield event


def tool_results(messages):
    return [block["toolResult"] for message in messages for block in message["content"]
            if "toolResult" in block]


LOCAL_CALLS = [
    pytest.param(catalog_tools, "hybrid_trip_search_with_spans", "hybrid_trip_search",
                 {"query": "Tokyo"}, id="hybrid_trip_search"),
    pytest.param(package_tools, "get_package_details", "get_package_details",
                 {"packageId": "CTY-002"}, id="get_package_details"),
]


@pytest.mark.parametrize("module, attribute, name, arguments", LOCAL_CALLS)
def test_a_local_tool_failure_reaches_the_model_as_a_stable_error_without_its_text(
    world, monkeypatch, caplog, module, attribute, name, arguments
):
    pool, _ = world

    async def failing(*args, **kwargs):
        raise RuntimeError("password=hunter2 in 123456789012")

    monkeypatch.setattr(module, attribute, failing)
    model = RecordingModel([tool_turn(name, arguments),
                            {"kind": "text", "text": "That read failed."}])

    turn = run(model, pool)

    [result] = tool_results(model.messages[1])
    assert result["status"] == "success"
    answer = json.loads(result["content"][0]["text"])
    assert answer == {"error": "catalog_tool_failed", "tool": name}
    assert "hunter2" not in json.dumps(model.messages)
    [logged] = [record for record in caplog.records if record.name == TOOLS_LOGGER]
    assert not logged.exc_info, "the raw exception stays out of the record"
    text = logged.getMessage()
    assert "RuntimeError" in text and "Traceback" in text
    assert "<acct>" in text and "123456789012" not in text
    assert "hunter2" not in text and "password=<masked>" in text
    assert "Tool call failed: " + name in [a.title for a in turn.activities]
    assert turn.message == "That read failed." and pool.releases == [7]


@pytest.mark.parametrize("module, attribute, name, arguments", LOCAL_CALLS)
def test_a_local_tool_that_could_not_be_set_up_still_ends_the_turn(
    world, monkeypatch, module, attribute, name, arguments
):
    pool, _ = world

    async def denied(*args, **kwargs):
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "no"}},
                          "ExecuteStatement")

    monkeypatch.setattr(module, attribute, denied)
    model = ScriptedModel([tool_turn(name, arguments), {"kind": "text", "text": "unused"}])

    with pytest.raises(phase3.RetrievalTurnError, match="could not be set up") as raised:
        run(model, pool)

    assert isinstance(raised.value.__cause__, ToolSetupError) and len(model.calls) == 1
    assert pool.releases == [7] and pool.resets == []


class ServingClient:
    """Stands in for a started MCP client that serves the given tools."""

    def __init__(self, tools):
        self.tools, self.stopped = tools, 0

    def start(self):
        return self

    def list_tools_sync(self):
        return list(self.tools)

    def stop(self, exc_type, exc_val, exc_tb):
        self.stopped += 1


class WedgedClient(ServingClient):
    """A server whose start never finishes until its client is stopped."""

    def __init__(self, tools):
        super().__init__(tools)
        self.closed = threading.Event()

    def start(self):
        self.closed.wait(3)
        raise RuntimeError("the client session is not running")

    def stop(self, exc_type, exc_val, exc_tb):
        super().stop(exc_type, exc_val, exc_tb)
        self.closed.set()


def inline(work, *args):
    work(*args)


def parked_catalog(seen, made, gate):
    """The stand-in catalog, whose price_range waits at ``gate`` and whose session has closed.

    region_inventory finds the session closed. price_range fails if the first client stopped
    while it was parked.
    """

    @tool(name="price_range")
    async def parked_price_range(destination: str) -> dict:
        """Price range.

        Args:
            destination: place.
        """
        gate["entered"].set()
        await gate["proceed"].wait()
        if made[0].stopped:
            raise RuntimeError("the client was stopped under a running call")
        return {"destination": destination, "low": 1.0, "average": 2.0, "high": 3.0}

    return replacing(fake_mcp_tools(seen), parked_price_range, closed_session_region_inventory)


async def park_a_turn(pool, gate):
    """Start a turn that stops inside its price_range call; fail at once if it ends sooner."""
    gate["entered"], gate["proceed"] = asyncio.Event(), asyncio.Event()
    running = asyncio.create_task(phase3.run_retrieval_turn(
        "Kyoto prices", traveler_id="trv_running", pool=pool, model=ScriptedModel([
            tool_turn("price_range", {"destination": "Kyoto"}),
            {"kind": "text", "text": "Kyoto trips run from 1 to 3."}])))
    entered = asyncio.ensure_future(gate["entered"].wait())
    await asyncio.wait({entered, running}, return_when=asyncio.FIRST_COMPLETED)
    assert not running.done(), f"the running turn ended early: {running.exception()!r}"
    return running


async def closing_turn(pool):
    """Another traveler's turn finds the MCP session closed: it fails and retires its client."""
    with pytest.raises(phase3.RetrievalTurnError, match="could not be set up"):
        await phase3.run_retrieval_turn(
            "Asia trips", traveler_id="trv_failing", pool=pool, model=ScriptedModel([
                tool_turn("region_inventory", {"region": "Asia"}),
                {"kind": "text", "text": "unused"}]))


def test_overlapping_turns_a_failed_turn_never_stops_the_client_the_other_turn_is_using(
    world, monkeypatch
):
    """Two travelers' turns share one pooled client; one fails while the other is mid tool call."""
    _, seen = world
    monkeypatch.setattr(mcp_session, "_run_detached", inline)
    made, gate = [], {}
    tools = parked_catalog(seen, made, gate)

    def factory():
        made.append(ServingClient(tools))
        return made[-1]

    pool = mcp_session.ConciergeMcpPool(factory)

    async def overlap():
        running = await park_a_turn(pool, gate)
        await closing_turn(pool)
        stopped_after_the_failure = [c.stopped for c in made]
        gate["proceed"].set()
        return stopped_after_the_failure, await running

    try:
        stopped_after_the_failure, turn = asyncio.run(overlap())
        assert stopped_after_the_failure == [0], "the running turn still holds the client"
        assert turn.message == "Kyoto trips run from 1 to 3."
        assert not [a for a in turn.activities if a.title.startswith("Tool call failed")]
        assert [c.stopped for c in made] == [1], "the retired client stops after its last turn"
        assert pool.acquire()[0] == 1 and len(made) == 2
    finally:
        pool.close()


def test_a_wedged_server_start_fails_only_the_turns_waiting_on_it(world, monkeypatch):
    """A turn holding a started client finishes while the turns waiting on a new start fail."""
    _, seen = world
    monkeypatch.setattr(mcp_session, "_run_detached", inline)
    made, gate = [], {}
    tools = parked_catalog(seen, made, gate)

    def factory():
        made.append(WedgedClient(tools) if len(made) == 1 else ServingClient(tools))
        return made[-1]

    pool = mcp_session.ConciergeMcpPool(factory, start_timeout=0.2)

    async def wedge():
        running = await park_a_turn(pool, gate)
        await closing_turn(pool)
        began = time.monotonic()
        waiting = await asyncio.gather(*(
            phase3.run_retrieval_turn("Tokyo", traveler_id=f"trv_{i}", pool=pool,
                                      model=ScriptedModel([]))
            for i in range(2)), return_exceptions=True)
        waited = time.monotonic() - began
        gate["proceed"].set()
        return waiting, waited, await running

    try:
        waiting, waited, turn = asyncio.run(wedge())
        assert waited < 1.5, "the waiting turns fail at the start deadline"
        assert [type(w) for w in waiting] == [phase3.RetrievalTurnError] * 2
        assert all("did not start" in str(w) for w in waiting)
        assert len(made) == 2, "the waiting turns shared one start"
        assert turn.message == "Kyoto trips run from 1 to 3."
        assert [c.stopped for c in made] == [1, 1], "the held client and the wedged one both stop"
    finally:
        pool.close()


def test_a_cancelled_acquire_leaves_the_counts_exact(world, monkeypatch):
    _, seen = world
    monkeypatch.setattr(mcp_session, "_run_detached", inline)
    starting, finish, released = threading.Event(), threading.Event(), threading.Event()

    class SlowStart(ServingClient):
        def start(self):
            starting.set()
            finish.wait(5)
            return self

    class Watched(mcp_session.ConciergeMcpPool):
        def release(self, generation):
            super().release(generation)
            released.set()

    client = SlowStart(fake_mcp_tools(seen))
    pool = Watched(lambda: client)

    async def cancel_while_acquiring():
        turn = asyncio.create_task(phase3.run_retrieval_turn(
            "Tokyo", traveler_id="trv_pinned", pool=pool, model=ScriptedModel([])))
        assert await asyncio.to_thread(starting.wait, 2)
        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn

    try:
        asyncio.run(cancel_while_acquiring())
        finish.set()
        assert released.wait(2), "the hold the cancelled turn never received went back"
        generation, _ = pool.acquire()
        pool.reset(generation)
        assert client.stopped == 1, "no turn still counts as holding the client"
    finally:
        pool.close()


def test_the_turn_timeout_covers_a_slow_acquire(world, monkeypatch):
    pool, _ = world
    unblock, released = threading.Event(), threading.Event()
    acquire, release = pool.acquire, pool.release

    def slow_acquire():
        unblock.wait(5)
        return acquire()

    def watched_release(generation):
        release(generation)
        released.set()

    pool.acquire, pool.release = slow_acquire, watched_release
    monkeypatch.setattr(phase3, "TURN_TIMEOUT_SECONDS", 0.05)
    began = time.monotonic()

    with pytest.raises(phase3.RetrievalTurnError, match="did not finish"):
        run(ScriptedModel([]), pool)

    assert time.monotonic() - began < 1
    unblock.set()
    assert released.wait(2) and pool.releases == [7] and pool.resets == []


def test_a_cancelled_turn_still_gives_its_client_back(world):
    pool, _ = world

    class Hanging(ScriptedModel):
        async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
            self.entered.set()
            await asyncio.sleep(30)
            yield {"messageStart": {"role": "assistant"}}

    async def cancel_mid_turn():
        model = Hanging([])
        model.entered = asyncio.Event()
        turn = asyncio.create_task(phase3.run_retrieval_turn(
            "Find me a Tokyo trip", traveler_id="trv_pinned", model=model, pool=pool))
        await model.entered.wait()
        turn.cancel()
        with pytest.raises(asyncio.CancelledError):
            await turn

    asyncio.run(cancel_mid_turn())

    assert pool.releases == [7] and pool.resets == [], "a cancel leaves the session usable"


def test_a_turn_that_fails_before_the_model_runs_still_gives_its_client_back(world, monkeypatch):
    pool, _ = world

    def broken(**kwargs):
        raise ValueError("duplicate tool name")

    monkeypatch.setattr(phase3, "build_agent", broken)

    with pytest.raises(ValueError, match="duplicate tool name"):
        run(ScriptedModel([]), pool)

    assert pool.releases == [7] and pool.resets == []


def test_a_completed_turn_gives_its_client_back_and_records_why_the_model_stopped(world):
    pool, _ = world

    turn = run(ScriptedModel([{"kind": "text", "text": "Hello."}]), pool)

    assert pool.releases == [7] and pool.resets == []
    assert turn.stop_reason == "end_turn"
    assert turn.activities[-1].details.endswith("stop reason end_turn")


def test_an_empty_reply_is_an_error_not_a_blank_message(world):
    pool, _ = world
    with pytest.raises(phase3.RetrievalTurnError, match="no reply"):
        run(ScriptedModel([{"kind": "text", "text": "   "}]), pool)


def test_tools_that_will_not_start_are_an_actionable_error(world):
    pool, _ = world
    pool.fail = RuntimeError("spawn failed")
    with pytest.raises(phase3.RetrievalTurnError, match="meridian-concierge"):
        run(ScriptedModel([]), pool)


def test_the_local_tools_are_the_ones_the_pool_does_not_serve():
    assert set(LOCAL_TOOLS) == {"hybrid_trip_search", "get_package_details"}


@tool(name="currency_convert")
def unsupported_currency_convert(amount: float, from_ccy: str, to_ccy: str) -> dict:
    """Convert.

    Args:
        amount: amount.
        from_ccy: from.
        to_ccy: to.
    """
    return {"error": "unsupported_currency", "currency": to_ccy}


def test_an_unsupported_currency_is_an_answer_that_keeps_the_client(world):
    _, seen = world
    pool = FakePool(replacing(fake_mcp_tools(seen), unsupported_currency_convert))
    model = RecordingModel([
        tool_turn("currency_convert", {"amount": 5, "from_ccy": "USD", "to_ccy": "XXX"}),
        {"kind": "text", "text": "I cannot convert to XXX."}])

    turn = run(model, pool)

    [result] = tool_results(model.messages[1])
    assert json.loads(result["content"][0]["text"]) == {
        "error": "unsupported_currency", "currency": "XXX"}
    assert pool.releases == [7] and pool.resets == []
    assert not [a for a in turn.activities if a.title.startswith("Tool call failed")]
    assert "Tool result: currency_convert" in [a.title for a in turn.activities]


def test_an_unknown_package_is_an_answer_not_a_failure(world, monkeypatch):
    pool, _ = world

    async def unknown(packageId):
        return {"error": "unknown_package", "package_id": packageId}

    monkeypatch.setattr(package_tools, "get_package_details", unknown)
    model = RecordingModel([tool_turn("get_package_details", {"packageId": "NOPE-1"}),
                            {"kind": "text", "text": "No such package."}])

    turn = run(model, pool)

    [result] = tool_results(model.messages[1])
    assert json.loads(result["content"][0]["text"]) == {
        "error": "unknown_package", "package_id": "NOPE-1"}
    assert pool.releases == [7] and pool.resets == []
    assert not [a for a in turn.activities if a.title.startswith("Tool call failed")]


def test_a_model_sees_an_mcp_error_only_as_the_stable_answer(world):
    _, seen = world
    pool = FakePool(replacing(fake_mcp_tools(seen), failing_price_range))
    model = RecordingModel([tool_turn("price_range", {"destination": "Kyoto"}),
                            {"kind": "text", "text": "Prices are unavailable."}])

    run(model, pool)

    [result] = tool_results(model.messages[1])
    assert json.loads(result["content"][0]["text"]) == {
        "error": "catalog_tool_failed", "tool": "price_range"}
    assert "wedged" not in json.dumps(model.messages)
    assert pool.resets == [7], "a result of an unknown shape fails toward recovery"


def test_the_pool_logs_a_failed_start_without_the_raw_account_id(world, caplog):
    pool, _ = world
    pool.fail = RuntimeError("no role arn:aws:iam::123456789012:role/x")

    with caplog.at_level(logging.ERROR, logger=phase3.__name__):
        with pytest.raises(phase3.RetrievalTurnError):
            run(ScriptedModel([]), pool)

    assert "Traceback" in caplog.text and "<acct>" in caplog.text
    assert "123456789012" not in caplog.text


def test_a_failed_agent_turn_logs_without_the_raw_account_id(world, caplog):
    class Leaking(ScriptedModel):
        async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
            raise RuntimeError("denied for 123456789012")
            yield

    pool, _ = world
    with caplog.at_level(logging.ERROR, logger=phase3.__name__):
        with pytest.raises(phase3.RetrievalTurnError):
            run(Leaking([]), pool)

    assert "Traceback" in caplog.text and "denied for <acct>" in caplog.text
    assert "123456789012" not in caplog.text


def test_an_unregistered_tool_name_is_not_an_mcp_failure(world):
    pool, _ = world
    model = RecordingModel([tool_turn("no_such_tool", {}),
                            {"kind": "text", "text": "I could not do that."}])

    turn = run(model, pool)

    [result] = tool_results(model.messages[1])
    assert result["status"] == "error" and "Unknown tool" in result["content"][0]["text"]
    assert pool.resets == [] and pool.releases == [7]
    assert turn.message == "I could not do that."


def _seconds_in(path: str, pattern: str) -> float:
    text = (Path(__file__).resolve().parents[1] / path).read_text(encoding="utf-8")
    found = re.search(pattern, text)
    assert found, f"{path} no longer matches {pattern}"
    return float(found.group(1).replace("_", ""))


def test_the_turn_limit_ends_before_the_browser_and_cloudfront_give_up():
    browser = _seconds_in("frontend/src/api/request.ts",
                          r"REQUEST_TIMEOUT_MS = ([\d_]+)") / 1000
    cloudfront = _seconds_in("infra/lib/meridian-web-stack.ts",
                             r"readTimeout: Duration\.seconds\((\d+)\)")

    assert phase3.TURN_TIMEOUT_SECONDS < min(browser, cloudfront)
