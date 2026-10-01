"""The Runtime times its Gateway tools/list and Memory list_events calls directly.

The clock here only moves inside the fake calls, by a fixed amount each. A span
that shows exactly that amount was timed around that call; opening the gateway
session or creating the Memory client must not count.

The Runtime's own SDK (``bedrock_agentcore``) is not installed in the backend
venv, so its three imports are stubbed. Everything the timed code runs is the
Runtime's real ``main.py``.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

RUNTIME = Path(__file__).resolve().parents[1] / "meridian_agentcore" / "app" / "MeridianConcierge"
sys.path.insert(0, str(RUNTIME))

SDK_MODULES = (
    "bedrock_agentcore",
    "bedrock_agentcore.memory",
    "bedrock_agentcore.memory.integrations",
    "bedrock_agentcore.memory.integrations.strands",
    "bedrock_agentcore.memory.integrations.strands.config",
    "bedrock_agentcore.memory.integrations.strands.session_manager",
    "bedrock_agentcore.runtime",
)


class FakeClock:
    """A perf_counter that only advances when a fake call says so."""

    def __init__(self) -> None:
        self.now = 1000.0

    def read(self) -> float:
        return self.now

    def spend(self, ms: int) -> None:
        self.now += ms / 1000


class FakeApp:
    def entrypoint(self, handler):
        return handler


class FakeMemory:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock

    def list_events(self, **_kwargs):
        self.clock.spend(45)
        return {"events": [{"eventId": "e1"}, {"eventId": "e2"}]}


class FakeSession:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock

    def client(self, _service):
        self.clock.spend(30)
        return FakeMemory(self.clock)

    def get_credentials(self):
        return None


def _fake_gateway(clock: FakeClock):
    class FakeGateway:
        def __init__(self, **_kwargs) -> None:
            pass

        def __enter__(self):
            clock.spend(300)
            return self

        def __exit__(self, *_exc):
            return False

        def list_tools_sync(self):
            clock.spend(120)
            return [SimpleNamespace(tool_name="MeridianSearch___semantic_trip_search")]

    return FakeGateway


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def runtime(monkeypatch, clock):
    for name in SDK_MODULES:
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    config = sys.modules["bedrock_agentcore.memory.integrations.strands.config"]
    config.AgentCoreMemoryConfig = config.RetrievalConfig = object
    manager = sys.modules["bedrock_agentcore.memory.integrations.strands.session_manager"]
    manager.AgentCoreMemorySessionManager = object
    sys.modules["bedrock_agentcore.runtime"].BedrockAgentCoreApp = FakeApp
    monkeypatch.setenv("AGENTCORE_GATEWAY_MERIDIAN_AURORA_URL", "https://gw-1.gateway.example/mcp")
    monkeypatch.setenv("MEMORY_MERIDIAN_SESSION_ID", "mem-1")
    spec = importlib.util.spec_from_file_location("meridian_runtime_main", RUNTIME / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fake_time = SimpleNamespace(perf_counter=clock.read, monotonic=clock.read)
    monkeypatch.setattr(module, "time", fake_time)
    monkeypatch.setattr(module, "SESSION", FakeSession(clock))
    return module


def test_memory_span_carries_only_the_list_events_time(runtime):
    span = runtime.memory_span("trv_meridian_demo", "conv-1")
    assert span["title"] == "AgentCore Memory · session restored"
    assert span["execution_time_ms"] == 45


async def _first(events, count: int) -> list:
    taken = []
    async for event in events:
        taken.append(event)
        if len(taken) == count:
            break
    await events.aclose()
    return taken


def test_tools_list_span_carries_only_the_list_tools_time(runtime, clock, monkeypatch):
    monkeypatch.setattr(runtime, "MCPClient", _fake_gateway(clock))
    payload = {"event": "concierge_turn", "traveler_id": "trv_meridian_demo",
               "conversation_id": "conv-1"}
    started, tools, memory = asyncio.run(_first(runtime.run(payload), 3))

    assert started["title"] == "AgentCore Runtime · turn started"
    assert started["execution_time_ms"] is None
    assert tools["title"] == "AgentCore Gateway · tools/list"
    # Opening the gateway session (300 ms) is not the tools/list call.
    assert tools["execution_time_ms"] == 120
    assert memory["execution_time_ms"] == 45
