"""One stdio client per process serves every Phase 3 turn, and a broken one is replaced."""

import threading
import time
from concurrent.futures import Future
from types import SimpleNamespace

import pytest

from backend.agents.concierge_definition import PHASE3_TOOLS, WRITE_TOOLS
from backend.agents.phase_03_retrieval import mcp_session as session
from backend.agents.phase_03_retrieval.tools import LOCAL_TOOLS

RUN_DETACHED = getattr(session, "_run_detached", None)


@pytest.fixture(autouse=True)
def stops_inline(monkeypatch):
    """Stop retired clients inline, so a test reads exact stop counts; one test uses the thread."""
    monkeypatch.setattr(session, "_run_detached", lambda work, *args: work(*args), raising=False)


class FakeClient:
    def __init__(self, names=None, fail_on_start=None):
        self.names = list(names if names is not None else session.MCP_TOOLS)
        self.fail_on_start = fail_on_start
        self.started = self.stopped = 0

    def start(self):
        self.started += 1
        if self.fail_on_start:
            raise self.fail_on_start
        return self

    def list_tools_sync(self):
        return [SimpleNamespace(tool_name=name) for name in self.names]

    def stop(self, exc_type, exc_val, exc_tb):
        self.stopped += 1


def making():
    """A factory that records every client it makes."""
    made = []

    def factory():
        made.append(FakeClient())
        return made[-1]

    return made, factory


class HangingClient(FakeClient):
    """A server that never answers ``tools/list`` until its client stops, like a wedged session."""

    def __init__(self, listing):
        super().__init__()
        self.listing, self.closed = listing, threading.Event()

    def list_tools_sync(self):
        self.listing.set()
        self.closed.wait(3)
        raise RuntimeError("the client session is not running")

    def stop(self, exc_type, exc_val, exc_tb):
        super().stop(exc_type, exc_val, exc_tb)
        self.closed.set()


def making_with_a_hang(hanging):
    """A factory whose client number ``hanging`` (from 0) wedges, setting ``listing`` as it does."""
    made, listing = [], threading.Event()

    def factory():
        made.append(HangingClient(listing) if len(made) == hanging else FakeClient())
        return made[-1]

    return made, factory, listing


def test_mcp_tools_are_the_phase_three_tools_the_process_does_not_run_itself():
    assert set(session.MCP_TOOLS) | set(LOCAL_TOOLS) == set(PHASE3_TOOLS)
    assert not set(session.MCP_TOOLS) & set(LOCAL_TOOLS)
    assert not set(PHASE3_TOOLS) & set(WRITE_TOOLS)


def test_the_client_starts_once_and_is_reused_by_every_turn():
    made, factory = making()
    pool = session.ConciergeMcpPool(factory)

    (first_generation, first), (second_generation, second) = pool.acquire(), pool.acquire()

    assert len(made) == 1 and made[0].started == 1
    assert first_generation == second_generation
    assert [t.tool_name for t in first] == list(session.MCP_TOOLS) == [t.tool_name for t in second]


def test_concurrent_first_turns_start_one_client():
    made, factory = making()
    pool = session.ConciergeMcpPool(factory)
    threads = [threading.Thread(target=pool.acquire) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(made) == 1


def test_reset_stops_the_client_and_the_next_turn_starts_a_fresh_one():
    made, factory = making()
    pool = session.ConciergeMcpPool(factory)
    generation, _ = pool.acquire()
    pool.reset(generation)
    pool.acquire()

    assert [c.stopped for c in made] == [1, 0] and len(made) == 2


def test_a_failed_turn_never_stops_the_client_another_turn_is_using():
    made, factory = making()
    pool = session.ConciergeMcpPool(factory)
    failing, _ = pool.acquire()
    running, _ = pool.acquire()

    pool.reset(failing)

    assert made[0].stopped == 0, "the other turn still holds the client"
    fresh, _ = pool.acquire()
    assert fresh != running and len(made) == 2
    pool.release(running)
    assert [c.stopped for c in made] == [1, 0]
    pool.release(fresh)
    assert made[1].stopped == 0, "a released current client stays up for the next turn"


def test_a_late_reset_while_the_replacement_starts_never_retires_the_replacement(monkeypatch):
    """Two turns fail on one client; the second reset lands while the next turn starts a new one."""
    made, factory = making()
    pool = session.ConciergeMcpPool(factory)
    failed, _ = pool.acquire()
    failing_later, _ = pool.acquire()
    pool.reset(failed)
    late = threading.Thread(target=pool.reset, args=(failing_later,))
    start = FakeClient.start

    def start_as_the_late_reset_arrives(client):
        late.start()
        late.join(0.2)
        return start(client)

    monkeypatch.setattr(FakeClient, "start", start_as_the_late_reset_arrives)
    replacement, _ = pool.acquire()
    monkeypatch.setattr(FakeClient, "start", start)
    late.join()

    assert replacement == failed + 1
    assert [c.stopped for c in made] == [1, 0], "the retired client stops; the replacement runs"
    again, _ = pool.acquire()
    assert again == replacement and len(made) == 2
    pool.close()


def test_a_start_that_hangs_past_its_timeout_fails_every_waiter_and_is_stopped():
    made, factory, _ = making_with_a_hang(1)
    pool = session.ConciergeMcpPool(factory, start_timeout=0.2)
    holding, _ = pool.acquire()
    failed, _ = pool.acquire()
    pool.reset(failed)
    outcomes = []

    def waiting_turn():
        try:
            outcomes.append(pool.acquire())
        except TimeoutError as error:
            outcomes.append(error)

    waiters = [threading.Thread(target=waiting_turn) for _ in range(3)]
    began = time.monotonic()
    for waiter in waiters:
        waiter.start()
    for waiter in waiters:
        waiter.join(5)

    assert len(outcomes) == 3 and all(isinstance(o, TimeoutError) for o in outcomes)
    assert time.monotonic() - began < 1.5, "the waiters share one start and its deadline"
    assert len(made) == 2 and made[1].stopped == 1, "the abandoned start is stopped"
    assert made[0].stopped == 0, "the turn holding the retired client keeps it"
    pool.release(holding)
    assert made[0].stopped == 1
    assert pool.acquire()[0] == failed + 1 and len(made) == 3, "the next turn starts afresh"
    pool.close()


def test_release_and_reset_return_promptly_while_a_start_hangs():
    made, factory, listing = making_with_a_hang(1)
    pool = session.ConciergeMcpPool(factory, start_timeout=5)
    first, _ = pool.acquire()
    second, _ = pool.acquire()
    third, _ = pool.acquire()
    pool.reset(first)
    waiter = threading.Thread(target=lambda: pytest.raises(RuntimeError, pool.acquire))
    waiter.start()
    assert listing.wait(2), "the replacement's start is under way"

    began = time.monotonic()
    pool.release(second)
    pool.reset(third)

    assert time.monotonic() - began < 0.1
    assert made[0].stopped == 1, "the retired client stopped once its last turn gave it back"
    pool.close()
    waiter.join(5)
    assert made[1].stopped == 1 and not waiter.is_alive()


def test_a_give_back_never_waits_for_a_retired_client_to_stop(monkeypatch):
    monkeypatch.setattr(session, "_run_detached", RUN_DETACHED)
    stopping, finish = threading.Event(), threading.Event()

    class SlowToStop(FakeClient):
        def stop(self, exc_type, exc_val, exc_tb):
            stopping.set()
            finish.wait(1)
            super().stop(exc_type, exc_val, exc_tb)

    client = SlowToStop()
    pool = session.ConciergeMcpPool(lambda: client)
    generation, _ = pool.acquire()

    began = time.monotonic()
    pool.reset(generation)

    assert time.monotonic() - began < 0.1
    assert stopping.wait(2), "the retired client is still stopped, on its own thread"
    finish.set()


def test_close_stops_every_client_held_or_not():
    made, factory = making()
    pool = session.ConciergeMcpPool(factory)
    held, _ = pool.acquire()
    pool.reset(held)
    pool.acquire()

    pool.close()

    assert [c.stopped for c in made] == [1, 1]


def test_a_client_that_fails_to_start_is_stopped_and_the_error_surfaces():
    client = FakeClient(fail_on_start=RuntimeError("boom"))
    pool = session.ConciergeMcpPool(lambda: client)

    with pytest.raises(RuntimeError, match="boom"):
        pool.acquire()

    assert client.stopped == 1


def test_a_server_that_offers_the_wrong_tools_is_refused_and_named():
    extra = FakeClient(names=[*session.MCP_TOOLS, "create_courtesy_hold"])
    pool = session.ConciergeMcpPool(lambda: extra)

    with pytest.raises(RuntimeError, match="create_courtesy_hold") as refused:
        pool.acquire()

    assert "unexpected" in str(refused.value) and extra.stopped == 1
    missing = FakeClient(names=list(session.MCP_TOOLS)[:-1])
    with pytest.raises(RuntimeError, match="missing"):
        session.ConciergeMcpPool(lambda: missing).acquire()


def test_the_real_stdio_server_offers_exactly_the_mcp_tools():
    pool = session.ConciergeMcpPool()
    try:
        _, tools = pool.acquire()
    finally:
        pool.close()
    assert {tool.tool_name for tool in tools} == set(session.MCP_TOOLS)


def test_a_real_mcp_client_receives_compare_packages_as_a_structured_result():
    """Backs the claim that a typed tool returns structured content through an MCP client.

    An empty id list reads nothing, so the call needs no database.
    """
    pool = session.ConciergeMcpPool()
    try:
        _, tools = pool.acquire()
        compare = next(tool for tool in tools if tool.tool_name == "compare_packages")
        result = compare.mcp_client.call_tool_sync(
            tool_use_id="t1", name="compare_packages", arguments={"package_ids": []})
    finally:
        pool.close()
    assert result["status"] == "success"
    assert result["structuredContent"] == {"result": []}


def test_the_module_pool_is_one_object():
    assert session.concierge_pool() is session.concierge_pool()


class RacingFuture(Future):
    """A start whose wait expires just as the start finishes: result() raises, done() is true."""

    expired = False

    def result(self, timeout=None):
        outcome = super().result()
        if self.expired:
            return outcome
        self.expired = True
        raise TimeoutError("the wait expired a moment before the start finished")


def test_a_start_that_finishes_as_the_wait_expires_is_not_reported_as_a_timeout(monkeypatch):
    monkeypatch.setattr(session, "Future", RacingFuture)
    made, factory = making()
    pool = session.ConciergeMcpPool(factory)

    generation, tools = pool.acquire()

    assert len(made) == 1 and made[0].stopped == 0
    assert [t.tool_name for t in tools] == list(session.MCP_TOOLS)
    assert pool.acquire()[0] == generation and len(made) == 1


def test_a_start_that_failed_as_the_wait_expires_reports_its_own_error(monkeypatch):
    monkeypatch.setattr(session, "Future", RacingFuture)
    pool = session.ConciergeMcpPool(lambda: FakeClient(fail_on_start=RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        pool.acquire()
