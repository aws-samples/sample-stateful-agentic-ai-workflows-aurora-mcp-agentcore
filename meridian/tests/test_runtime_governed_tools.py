"""The Runtime gives the model only the Gateway tools whose calls the audit row can list.

TraceHooks emits spans only for the tools in SPANS, and the audit row lists a call only from its
spans. With semantic search on, the Gateway also lists a tool it provisions itself,
x_amz_bedrock_agentcore_search. A call to it would run with no span and no entry in tool_calls,
so the Runtime withholds it from the model, and the tools/list span names what it withheld.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.runtime_support import load_runtime_main
from tests.test_runtime_span_timings import FakeClock, FakeSession

RUNTIME = Path(__file__).resolve().parents[1] / "meridian_agentcore" / "app" / "MeridianConcierge"
sys.path.insert(0, str(RUNTIME))

from turn_trace import SPANS, short  # noqa: E402

GOVERNED = [
    "MeridianCatalog___search_trips",
    "MeridianCatalog___compare_packages",
    "MeridianCatalog___price_range",
    "MeridianCatalog___region_inventory",
    "MeridianCatalog___currency_convert",
    "MeridianCatalog___hybrid_trip_search",
    "MeridianHolds___get_package_details",
    "MeridianHolds___loyalty_balance",
    "MeridianHolds___create_courtesy_hold",
    "MeridianHolds___confirm_booking",
]
GATEWAY_SEARCH = "x_amz_bedrock_agentcore_search"
LISTED = [GOVERNED[0], GATEWAY_SEARCH, *GOVERNED[1:]]
PAYLOAD = {"event": "concierge_turn", "traveler_id": "trv_meridian_demo",
           "conversation_id": "conv-1", "prompt": "Find Tokyo trips"}


def listed(names):
    return [SimpleNamespace(tool_name=name) for name in names]


@pytest.fixture
def runtime(monkeypatch):
    module = load_runtime_main(monkeypatch, "meridian_runtime_tools")
    module.SESSION = FakeSession(FakeClock())
    module.memory_session = lambda: module.SESSION
    module.built = []

    class Gateway:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def list_tools_sync(self):
            return listed(LISTED)

    class FakeAgent:
        async def stream_async(self, _prompt):
            yield {"data": "Two trips fit."}

    def build_agent(**kwargs):
        module.built.append(kwargs)
        return FakeAgent()

    module.MCPClient = Gateway
    module.build_agent = build_agent
    module.memory_manager = lambda *_args: None
    module.concierge_model = lambda: None
    return module


async def collect(events):
    return [event async for event in events]


def labels(span):
    return {field["label"]: field["value"] for field in span["telemetry"]["fields"]}


def test_the_governed_names_cover_every_tool_with_a_span():
    assert {short(name) for name in GOVERNED} == set(SPANS)


def test_the_model_receives_every_tool_with_a_span_and_nothing_else(runtime):
    asyncio.run(collect(runtime.run(PAYLOAD)))

    (agent,) = runtime.built
    assert [tool.tool_name for tool in agent["tools"]] == GOVERNED


def test_the_tools_list_span_names_every_listed_tool_and_the_ones_withheld(runtime):
    events = asyncio.run(collect(runtime.run(PAYLOAD)))

    (span,) = [event for event in events if event.get("title") == "AgentCore Gateway: tools/list"]
    assert labels(span)["tools"] == ", ".join(LISTED)
    assert labels(span)["withheld_from_model"] == GATEWAY_SEARCH


def test_with_only_governed_tools_listed_nothing_is_withheld(runtime):
    assert labels(runtime.tools_span(listed(GOVERNED), 1))["withheld_from_model"] == "none"
