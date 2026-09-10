"""Meridian concierge runtime.

A Strands agent plans with tools served by AgentCore Gateway over MCP, keeps its
conversation in AgentCore Memory, and streams activity spans back to the backend.
Every tool call is signed with the runtime's execution role and checked by the
gateway's Cedar policies before any Lambda runs.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import boto3
from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    RetrievalConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from opentelemetry import trace
from strands import Agent
from strands.models.bedrock import BedrockModel
from strands.tools.mcp import MCPClient

from gateway_auth import GatewaySigV4
from hold_execution import execute_confirmed_hold
from prompts import narration_prompt, system_prompt, turn_prompt
from turn_trace import TraceHooks, TurnContext, activity

app = BedrockAgentCoreApp()
REGION = os.getenv("AWS_REGION", "us-east-1")
SESSION = boto3.Session(region_name=REGION)
GATEWAY_URL = os.environ["AGENTCORE_GATEWAY_MERIDIAN_AURORA_URL"]
MEMORY_ID = os.environ["MEMORY_MERIDIAN_SESSION_ID"]
MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-5")
GATEWAY_ID = os.getenv("MERIDIAN_GATEWAY_ID", GATEWAY_URL.split("//")[-1].split(".")[0])
POLICY_ENGINE_ID = os.getenv("MERIDIAN_POLICY_ENGINE_ID", "")
SESSION_NAMESPACE = "/users/{actorId}/sessions/{sessionId}"
FOLLOW_UPS = [
    "Compare the top options",
    "Check duration availability",
    "Explain the preference match",
]


def trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None


async def drive(agent, prompt, queue):
    try:
        async for part in agent.stream_async(prompt):
            await queue.put(("part", part))
    except Exception as error:  # noqa: BLE001 - surfaced to the backend as an error event
        await queue.put(("error", error))
    finally:
        await queue.put(("end", None))


def memory_manager(traveler_id: str, conversation_id: str) -> AgentCoreMemorySessionManager:
    config = AgentCoreMemoryConfig(
        memory_id=MEMORY_ID,
        session_id=conversation_id,
        actor_id=traveler_id,
        retrieval_config={SESSION_NAMESPACE: RetrievalConfig(top_k=5, relevance_score=0.3)},
    )
    return AgentCoreMemorySessionManager(agentcore_memory_config=config, region_name=REGION)


def memory_span(traveler_id: str, conversation_id: str) -> dict:
    client = SESSION.client("bedrock-agentcore")
    events = client.list_events(
        memoryId=MEMORY_ID, actorId=traveler_id, sessionId=conversation_id, maxResults=50
    )
    count = len(events.get("events", []))
    return activity(
        "reasoning",
        "AgentCore Memory · session restored",
        f"{count} prior events for this conversation loaded by the session manager",
        {
            "category": "memory_short",
            "component": "Bedrock AgentCore Memory",
            "status": "ok",
            "fields": [
                {"label": "memory_id", "value": MEMORY_ID, "mono": True},
                {"label": "actor_id", "value": traveler_id, "mono": True},
                {"label": "session_id", "value": conversation_id, "mono": True},
                {"label": "events", "value": str(count)},
                {"label": "namespace", "value": SESSION_NAMESPACE, "mono": True},
            ],
        },
    )


def turn_context(payload: dict) -> tuple[TurnContext, dict | None]:
    hold_target = payload.get("hold_target") or None
    turn = TurnContext(
        traveler_id=str(payload.get("traveler_id") or "trv_meridian_demo"),
        conversation_id=str(payload.get("conversation_id") or "conv-unknown"),
        hold_confirmed=bool(payload.get("hold_confirmed")) and hold_target is not None,
        budget_ceiling_cents=int(payload.get("budget_ceiling_cents") or 0),
        gateway_id=GATEWAY_ID,
        policy_engine_id=POLICY_ENGINE_ID,
    )
    return turn, hold_target


def start_span(turn: TurnContext) -> dict:
    return activity(
        "runtime",
        "AgentCore Runtime · turn started",
        f"microVM session for {turn.conversation_id}",
        {
            "category": "runtime",
            "component": "Bedrock AgentCore Runtime · MeridianConcierge",
            "status": "ok",
            "fields": [
                {"label": "trace_id", "value": trace_id() or "pending", "mono": True},
                {"label": "model", "value": MODEL_ID, "mono": True},
                {"label": "hold_confirmed", "value": str(turn.hold_confirmed).lower()},
            ],
        },
    )


def tools_span(tools: list) -> dict:
    return activity(
        "tool_call",
        "AgentCore Gateway · tools/list",
        f"{len(tools)} MCP tools discovered with IAM-signed requests",
        {
            "category": "gateway",
            "component": "Bedrock AgentCore Gateway",
            "status": "ok",
            "fields": [
                {"label": "endpoint", "value": GATEWAY_URL, "mono": True},
                {"label": "tools", "value": ", ".join(t.tool_name for t in tools)},
                {"label": "policy_engine", "value": POLICY_ENGINE_ID or "none", "mono": True},
            ],
        },
    )


def drain(queue):
    """Yield the events the hooks queued during a platform-executed tool call."""
    while not queue.empty():
        kind, item = queue.get_nowait()
        if kind == "activity":
            yield {"type": "activity", **item}
        elif kind == "hold":
            yield {"type": "hold", **item}
        elif kind == "packages":
            yield {"type": "packages", "packages": item}


async def pump(queue, task):
    """Yield runtime events until the agent finishes; return the answer and usage."""
    answer, usage = "", {}
    while True:
        kind, item = await queue.get()
        if kind == "activity":
            yield {"type": "activity", **item}
        elif kind == "packages":
            yield {"type": "packages", "packages": item}
        elif kind == "hold":
            yield {"type": "hold", **item}
        elif kind == "part":
            data = item.get("data")
            if data:
                answer += data
                yield {"type": "token", "text": data}
            elif "result" in item:
                usage = dict(item["result"].metrics.accumulated_usage)
        elif kind == "error":
            await task
            raise item
        elif kind == "end":
            break
    await task
    yield {"type": "answer", "text": answer, "usage": usage}


async def run(payload: dict):
    started = time.monotonic()
    turn, hold_target = turn_context(payload)
    queue: asyncio.Queue = asyncio.Queue()
    hooks = TraceHooks(queue, turn)
    gateway = MCPClient(url=GATEWAY_URL, auth_provider=GatewaySigV4(SESSION, REGION))
    yield {"type": "activity", **start_span(turn)}
    with gateway:
        tools = gateway.list_tools_sync()
        yield {"type": "activity", **tools_span(tools)}
        yield {"type": "activity", **memory_span(turn.traveler_id, turn.conversation_id)}
        outcome = None
        if turn.hold_confirmed and hold_target:
            # The click is the confirmation: the platform places the governed call with
            # the exact confirmed terms, and the model narrates what the gateway decided.
            outcome = execute_confirmed_hold(
                hooks,
                lambda tool_use_id, name, args: gateway.call_tool_sync(
                    tool_use_id=tool_use_id, name=name, arguments=args
                ),
                hold_target,
            )
            for event in drain(queue):
                yield event
        agent = Agent(
            model=BedrockModel(model_id=MODEL_ID, region_name=REGION, max_tokens=1500),
            system_prompt=system_prompt(turn.hold_confirmed, hold_target),
            tools=[] if outcome is not None else tools,
            hooks=[hooks],
            session_manager=memory_manager(turn.traveler_id, turn.conversation_id),
            callback_handler=None,
        )
        if outcome is not None:
            prompt = narration_prompt(outcome, hold_target)
        else:
            prompt = turn_prompt(
                str(payload.get("prompt", "")),
                str(payload.get("memory_context", "")),
                hold_target,
                turn.hold_confirmed,
            )
        task = asyncio.create_task(drive(agent, prompt, queue))
        answer, usage = "", {}
        async for event in pump(queue, task):
            if event["type"] == "answer":
                answer, usage = event["text"], event["usage"]
            else:
                yield event
    if not answer.strip():
        answer = "I could not produce a grounded recommendation from the live options."
    yield {
        "type": "result",
        "message": answer.strip(),
        "recommended_package_ids": [
            p.get("package_id") for p in hooks.packages if p.get("package_id")
        ],
        "follow_ups": FOLLOW_UPS,
        "hold": hooks.hold,
        "trace_id": trace_id(),
        "usage": usage,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }


@app.entrypoint
async def invoke(payload, context=None):
    if payload.get("event") != "concierge_turn":
        yield json.dumps({"type": "error", "message": "Unsupported Meridian Runtime event."})
        return
    try:
        async for item in run(payload):
            yield json.dumps(item, ensure_ascii=False)
    except Exception as error:  # noqa: BLE001 - the backend renders the failure as a span
        yield json.dumps({"type": "error", "message": str(error)[:600]})


if __name__ == "__main__":
    app.run()
