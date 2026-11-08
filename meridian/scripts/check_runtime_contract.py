"""Offline integration checks using one Runtime's exact pyproject dependencies.

Run with that Runtime's virtualenv, not the backend environment. MCP travels over
stdio to a real child server; all business responses and snapshot storage are local.
Both processes refuse TCP connections, so this cannot contact AWS.
"""

import asyncio
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import sys
import tomllib
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
APPS = ROOT / "meridian_agentcore/app"


def offline():
    """Block network access before importing any SDK, including in the MCP child."""
    original = socket.socket.connect

    def connect(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("Runtime contract check attempted network access")
        return original(sock, address)

    socket.socket.connect = connect
    os.environ.update(
        AWS_ACCESS_KEY_ID="offline",
        AWS_SECRET_ACCESS_KEY="offline",
        AWS_EC2_METADATA_DISABLED="true",
        AWS_DEFAULT_REGION="us-east-1",
    )
    for key in ("AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_DEFAULT_PROFILE"):
        os.environ.pop(key, None)


def verify_pins(runtime):
    project = tomllib.loads((APPS / runtime / "pyproject.toml").read_text())
    pins = project["project"]["dependencies"] + project["tool"]["uv"]["constraint-dependencies"]
    for pin in pins:
        package, expected = pin.split("==")
        package = package.split("[")[0]
        actual = importlib.metadata.version(package)
        assert actual == expected, f"{runtime}: {package} is {actual}, expected {expected}"


async def server():
    from mcp import types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server

    async def list_tools(ctx, params):
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="MeridianHolds___create_courtesy_hold",
                    description="Offline contract fixture",
                    inputSchema={"type": "object", "properties": {}},
                )
            ]
        )

    async def call_tool(ctx, params):
        args = params.arguments
        assert args["travelerId"] == "traveler" and args["verifiedSubject"] == "subject"
        assert args["authorizationRef"] and args["travelerConfirmed"] is True
        result = (
            {"error": "hold_outcome_unknown"}
            if args["packageId"] == "UNKNOWN"
            else {
                "hold": {
                    "bookingId": "HLD-LOCAL",
                    "status": "held",
                    "packageId": "PKG-001",
                    "duration": "2 nights",
                    "travelers": 1,
                    "totalAmount": "100.00",
                    "expiresAt": "2026-11-08T00:00:00Z",
                    "seatsRemaining": 0,
                },
                "governance": {
                    "decision": "allow",
                    "action_audit_id": "action-local",
                    "gateway_request_id": "gateway-local",
                },
            }
        )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(result))]
        )

    app = Server("runtime-contract", on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (reader, writer):
        await app.run(reader, writer, app.create_initialization_options())


def concierge():
    from mcp import StdioServerParameters
    from mcp.client.stdio import stdio_client
    from strands.tools.mcp import MCPClient
    from hold_execution import execute_confirmed_hold
    from turn_trace import TraceHooks, TurnContext

    parameters = StdioServerParameters(command=sys.executable, args=[__file__, "--server"])
    with MCPClient(lambda: stdio_client(parameters)) as client:
        assert len(client.list_tools_sync()) == 1
        for package in ("PKG-001", "UNKNOWN"):
            queue = asyncio.Queue()
            context = TurnContext(
                "traveler",
                "intent-1",
                True,
                400000,
                "gw",
                "pe",
                verified_subject="subject",
                gateway_auth="Bearer",
            )
            hooks = TraceHooks(queue, context)
            forged = SimpleNamespace(
                tool_use={
                    "name": "MeridianHolds___loyalty_balance",
                    "toolUseId": "forged",
                    "input": {"travelerId": "other", "verifiedSubject": "other"},
                },
                cancel_tool=False,
            )
            hooks.before(forged)
            assert forged.tool_use["input"]["verifiedSubject"] == "subject"
            assert forged.tool_use["input"]["travelerId"] == "traveler"
            result = execute_confirmed_hold(
                hooks,
                client.call_tool_sync,
                {
                    "package_id": package,
                    "duration": "2 nights",
                    "travelers": 1,
                    "unit_price_cents": 10000,
                },
            )
            if package == "UNKNOWN":
                assert "unknown" in result.lower() and hooks.hold is None
            else:
                assert "HLD-LOCAL" in result and hooks.hold["bookingId"] == "HLD-LOCAL"
                events = []
                while not queue.empty():
                    events.append(queue.get_nowait())
                assert "action-local" in json.dumps(events)


async def workflow():
    from strands.session import SnapshotSessionManager
    from strands.storage import InMemoryStorage
    from backend.agents.phase_05_workflow.graph import (
        NODE_IDS,
        ResumableStorage,
        RunContext,
        build_graph,
        pending_interrupts,
        run_task,
        snapshot_key,
    )

    storage, calls = InMemoryStorage(), []

    def fresh_graph(confirmed):
        nodes = SimpleNamespace()
        for name in NODE_IDS:

            async def step(state, config, name=name):
                calls.append(name)
                return {**state, "intent": "plan"}

            setattr(nodes, name, step)
        run = RunContext("contract", "execution", confirmed, True)
        session = SnapshotSessionManager(
            "contract", storage=ResumableStorage(storage), multi_agent_save_latest_on="node"
        )
        return build_graph(nodes, run, session)

    await fresh_graph(False).invoke_async(
        run_task(
            query="Flight cancelled. Rebuild my Tokyo trip.",
            traveler_id="traveler",
            conversation_id="contract",
            journey_id="journey",
            travelers_count=1,
        )
    )
    snapshot = json.loads(await storage.read(snapshot_key("contract")))
    interrupts = pending_interrupts(snapshot)
    assert interrupts and calls == ["classify", "search"]
    answers = [
        {"interruptResponse": {"interruptId": item["id"], "response": "approved"}}
        for item in interrupts
    ]
    result = await fresh_graph(True).invoke_async(answers)
    assert result.status.value == "completed"
    assert calls == ["classify", "search", "availability", "prepare_hold", "hold", "synthesize"]


def main():
    offline()
    if sys.argv[1:] == ["--server"]:
        asyncio.run(server())
        return
    runtime = sys.argv[1] if len(sys.argv) == 2 else ""
    if runtime not in ("MeridianConcierge", "MeridianWorkflow"):
        raise SystemExit("Usage: check_runtime_contract.py MeridianConcierge|MeridianWorkflow")
    verify_pins(runtime)
    sys.path.insert(0, str(APPS / runtime))
    os.environ.update(
        AGENTCORE_GATEWAY_MERIDIAN_AURORA_JWT_URL="https://gateway.example.test/mcp",
        MEMORY_MERIDIAN_SESSION_ID="contract-memory",
        MERIDIAN_AGENTCORE_AUTH="jwt",
    )
    entry = importlib.import_module("main")
    assert entry.app is not None
    if runtime == "MeridianConcierge":
        for mode, token in (("iam", None), ("jwt", "contract-token")):
            args, _ = entry.recording_arguments(
                entry.gateway_client_arguments(mode, entry.SESSION, entry.REGION, token))
            entry.MCPClient(url=entry.GATEWAY_URL, **args)
    if runtime == "MeridianConcierge":
        concierge()
    else:
        asyncio.run(workflow())
    print(f"{runtime}: exact dependency pins and offline execution contract passed")


if __name__ == "__main__":
    main()
