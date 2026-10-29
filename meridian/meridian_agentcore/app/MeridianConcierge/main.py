"""Meridian concierge runtime.

A Strands agent plans with tools served by AgentCore Gateway over MCP, keeps its
conversation in AgentCore Memory, and streams activity spans back to the backend.
Every tool call is authenticated to the gateway and checked by its Cedar policies before any
Lambda runs: signed with the runtime's execution role (``MERIDIAN_AGENTCORE_AUTH=iam``, the
default) or carrying the signed-in caller's access token (``jwt``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from contextlib import aclosing, asynccontextmanager
from concurrent.futures import Future
from datetime import timedelta
from functools import lru_cache

from tool_errors import StopOnToolSetupError, install_log_masking, mask_account_ids

install_log_masking()

import boto3
from botocore.config import Config
from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    RetrievalConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from opentelemetry import trace
from strands.tools.mcp import MCPClient

from caller_identity import CallerRefused, auth_mode, caller_from_request
from concierge_definition import WORKFLOW_ONLY_TOOLS, TravelerPinHook, build_agent
from gateway_auth import gateway_client_arguments, gateway_url, recording_arguments
from hold_execution import execute_confirmed_booking, execute_confirmed_hold
from model.load import DEFAULT_MODEL_ID, load_model
from prompts import narration_prompt, turn_instructions, turn_prompt
from turn_watchdog import KEEPALIVE_EVENT, TurnTimedOut, TurnWatchdog, settle_history
from turn_work import HistoryRepairs, OffLoopExecutor, wait_for_job
from turn_trace import SPANS, TraceHooks, TurnContext, activity, short

app = BedrockAgentCoreApp()
REGION = os.getenv("AWS_REGION", "us-east-1")
SESSION = boto3.Session(region_name=REGION)
GATEWAY_URL = gateway_url(os.environ)
MEMORY_ID = os.environ["MEMORY_MERIDIAN_SESSION_ID"]
MODEL_ID = os.getenv("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
GATEWAY_ID = os.getenv("MERIDIAN_GATEWAY_ID", GATEWAY_URL.split("//")[-1].split(".")[0])
POLICY_ENGINE_ID = os.getenv("MERIDIAN_POLICY_ENGINE_ID", "")
POLICY_MODE = os.getenv("MERIDIAN_POLICY_MODE", "ENFORCE")
SESSION_NAMESPACE = "/users/{actorId}/sessions/{sessionId}"
GATEWAY_AUTH_LABELS = {"iam": "SigV4", "jwt": "Cognito access token"}
REJECTION_POLL_SECONDS = 0.25
# Three limits over a turn, the first two counted from the proof of the caller.
#
# Idle limit. The backend's 45 second limit on InvokeAgentRuntime is a read timeout, the longest
# gap between received bytes (boto read_timeout in backend/agentcore/runtime.py and
# RuntimeHttpClient's TIMEOUT.read in backend/agentcore/runtime_https.py); its SDK retries are
# off, so a timeout is never retried. The turn therefore ends when nothing has happened for 35
# seconds, and a turn that keeps making progress can run longer. The worst case from the last
# progress to the fixed error leaving: 35 s idle + 1 s to end the agent task + 3 s to save the
# closed history + 2 s to close the Gateway client = 41 s, leaving 4 s of the 45.
#
# Keepalive. Progress the caller cannot see (model reasoning, tool-call arguments being
# streamed) must not leave it silent: after 15 s without an event for the caller the Runtime
# sends {"type": "keepalive"}, which the backend's reader passes over (iter_sse yields it,
# _forward_runtime_events and _apply_event ignore unknown types) and which is not progress.
#
# Calls that block the event loop. While one runs, no keepalive is sent and neither the watchdog
# nor the poll runs, so each must be bounded on its own. One remains on the loop:
#   - The confirmed platform write (call_tool_sync) waits at most the time the turn has left,
#     35 s, measured from the Memory span the caller was just sent. Worst silence: 35 s + 2 s
#     to close the Gateway client = 37 s, leaving 8 s.
# AgentCore Memory saves and retrievals are not on the loop. The session manager runs in
# async_mode: Strands awaits its MessageAddedEvent callbacks, which run append_message,
# sync_agent and retrieve_customer_context through asyncio.to_thread, so a slow or retried call
# costs wall time that the idle watchdog sees, and the keepalive and the poll keep running.
# Every Memory client gets MEMORY_CONNECT_SECONDS, MEMORY_READ_SECONDS and MEMORY_ATTEMPTS
# attempts in botocore's standard retry mode, so a throttled call is sent again. One call takes
# at most 3 x (2 + 4) s plus the backoff between attempts (at most 1 s, then 2 s) = 21 s, and a
# service retry-after hint can add up to 5 s to each wait. A message makes up to
# MEMORY_CALLS_PER_MESSAGE calls in a row, so a Memory outage ends the turn at the idle limit
# (35 s), not at the end of the chain. Those threads cannot be stopped: a turn that is ended
# while one runs abandons it, and its write lands later (see ``settle_history``).
# The agent build and the Gateway client start and close are not on the loop either: they run on
# a worker thread under the watchdog.
# Before it builds the agent, a turn waits up to SETTLE_SECONDS (3 s) for the previous turn's
# history repair; that silence is inside the idle limit.
#
# Hard cap. A model that keeps producing progress in a loop must still end. AgentCore Runtime
# allows 15 minutes for a synchronous request and 60 minutes for a response stream (Quotas for
# Amazon Bedrock AgentCore, "Invocation limits"). A Phase 4 turn is a few Gateway calls and one
# reply, tens of seconds. 300 s is several times any realistic turn and leaves 10 minutes
# below the 15 minute request limit for the Runtime to report the error.
CALLER_READ_SECONDS = 45
IDLE_LIMIT_SECONDS = 35
KEEPALIVE_SECONDS = 15
MAX_TURN_SECONDS = 300
CANCEL_GRACE_SECONDS = 1.0
SETTLE_SECONDS = 3.0
CLOSE_SECONDS = 2.0
MEMORY_CONNECT_SECONDS = 2.0
MEMORY_READ_SECONDS = 4.0
MEMORY_CALLS_PER_MESSAGE = 3
MEMORY_ATTEMPTS = 3
MEMORY_CALL_SECONDS = MEMORY_ATTEMPTS * (MEMORY_CONNECT_SECONDS + MEMORY_READ_SECONDS) + 1 + 2
# The Memory calls an abandoned turn leaves (up to MEMORY_CALL_SECONDS each) run through
# asyncio.to_thread on the loop's default executor, min(32, cpu + 4) threads, which a Memory
# outage can fill; a later turn's to_thread then waits behind them. Nothing can stop those
# threads, and the wait heals within one call bound as each gives up, so it is not handled.
ABANDONABLE_WORKERS = 8
OFFLOOP = OffLoopExecutor(ABANDONABLE_WORKERS)
REPAIRS = HistoryRepairs(OFFLOOP)
logger = logging.getLogger(__name__)
FOLLOW_UPS = [
    "Compare the top options",
    "Check duration availability",
    "Explain the preference match",
]


@lru_cache(maxsize=1)
def concierge_model():
    """Reuse the stateless model client within the isolated Runtime session."""
    return load_model(MODEL_ID, REGION)


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


def memory_client_config() -> Config:
    """The botocore settings of every AgentCore Memory client: short timeouts, bounded retries.

    Standard mode retries throttling and transient failures with jittered backoff, at most
    ``MEMORY_ATTEMPTS`` attempts in all, so one call is bounded by ``MEMORY_CALL_SECONDS``.
    """
    return Config(
        connect_timeout=MEMORY_CONNECT_SECONDS, read_timeout=MEMORY_READ_SECONDS,
        retries={"mode": "standard", "total_max_attempts": MEMORY_ATTEMPTS})


def memory_session() -> boto3.Session:
    """A boto3 session for one thread; sessions are not thread safe, clients are."""
    return boto3.Session(region_name=REGION)


def memory_manager(traveler_id: str, conversation_id: str) -> AgentCoreMemorySessionManager:
    config = AgentCoreMemoryConfig(
        memory_id=MEMORY_ID,
        session_id=conversation_id,
        actor_id=traveler_id,
        async_mode=True,
        retrieval_config={SESSION_NAMESPACE: RetrievalConfig(top_k=5, relevance_score=0.3)},
    )
    manager = AgentCoreMemorySessionManager(
        agentcore_memory_config=config, region_name=REGION, boto_session=memory_session(),
        boto_client_config=memory_client_config())
    watch_restore(manager)
    return manager


def watch_restore(manager: AgentCoreMemorySessionManager) -> None:
    """Record on ``manager.restore_failed`` that a ``list_events`` call of its client raised.

    The SDK's ``list_messages`` logs the error and returns an empty history, and offers no other
    signal, so the one reliable place to see a failed restore is the client call underneath.
    The error still reaches the SDK, which behaves as before. ``make_agent`` reads the flag
    right after the restore, so only the restore's own calls count.
    """
    manager.restore_failed = False
    client = manager.memory_client
    list_events = client.list_events

    def watched(*args, **kwargs):
        try:
            return list_events(*args, **kwargs)
        except Exception:
            manager.restore_failed = True
            raise

    client.list_events = watched


def elapsed_ms(started: float) -> int:
    """Whole milliseconds since ``started``, a ``time.perf_counter()`` reading."""
    return round((time.perf_counter() - started) * 1000)


def memory_span(traveler_id: str, conversation_id: str) -> dict:
    """The Memory span, timed around its one ``list_events`` call and nothing else."""
    client = memory_session().client("bedrock-agentcore", config=memory_client_config())
    started = time.perf_counter()
    events = client.list_events(
        memoryId=MEMORY_ID, actorId=traveler_id, sessionId=conversation_id, maxResults=50
    )
    took = elapsed_ms(started)
    count = len(events.get("events", []))
    return activity(
        "reasoning",
        "AgentCore Memory: session restored",
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
        took,
    )


RESTORE_FAILED_TITLE = "AgentCore Memory: conversation history could not be restored"


def restore_failed_span(span: dict) -> dict:
    """The Memory span of a turn whose history could not be restored, with a fixed reason.

    The reason never carries the SDK's error text, which can name an account or a resource.
    """
    telemetry = span["telemetry"]
    return activity(
        "error",
        RESTORE_FAILED_TITLE,
        "The concierge continued this turn without the earlier messages of the conversation",
        {**telemetry, "status": "error",
         "fields": [field for field in telemetry["fields"] if field["label"] != "events"]},
        span["execution_time_ms"],
    )


def turn_context(
    payload: dict, traveler_id: str | None = None, gateway_auth: str = "SigV4"
) -> tuple[TurnContext, dict | None, dict | None]:
    """The authorized turn plus the hold or booking terms the traveler confirmed, if any.

    ``traveler_id`` is the traveler proved by the caller's token; without it the payload's is used.
    """
    hold_target = payload.get("hold_target") or None
    booking_target = payload.get("booking_target") or None
    turn = TurnContext(
        traveler_id=str(traveler_id or payload.get("traveler_id") or "trv_meridian_demo"),
        conversation_id=str(payload.get("conversation_id") or "conv-unknown"),
        hold_confirmed=bool(payload.get("hold_confirmed")) and hold_target is not None,
        budget_ceiling_cents=int(payload.get("budget_ceiling_cents") or 0),
        gateway_id=GATEWAY_ID,
        policy_engine_id=POLICY_ENGINE_ID,
        policy_mode=POLICY_MODE,
        booking_confirmed=bool(payload.get("booking_confirmed")) and booking_target is not None,
        gateway_auth=gateway_auth,
    )
    return turn, hold_target, booking_target


def start_span(turn: TurnContext) -> dict:
    return activity(
        "runtime",
        "AgentCore Runtime: turn started",
        f"microVM session for {turn.conversation_id}",
        {
            "category": "runtime",
            "component": "Bedrock AgentCore Runtime, MeridianConcierge",
            "status": "ok",
            "fields": [
                {"label": "trace_id", "value": trace_id() or "pending", "mono": True},
                {"label": "model", "value": MODEL_ID, "mono": True},
                {"label": "hold_confirmed", "value": str(turn.hold_confirmed).lower()},
                {"label": "booking_confirmed", "value": str(turn.booking_confirmed).lower()},
            ],
        },
    )


def governed_tools(tools: list) -> tuple[list, list[str]]:
    """Split the listed tools into the ones the model may call and the names withheld from it.

    TraceHooks records a call only for a tool in ``SPANS``, and each of those has a Cedar policy
    in ``POLICY_FOR``. The Gateway can list more, such as the search tool it provisions itself
    when semantic search is on. A call to one of those would leave no span and no entry in the
    audit row, so the model never receives it. A tool in ``WORKFLOW_ONLY_TOOLS`` is withheld
    by name, even if it later gains a span, because only the Phase 5 workflow may call it.
    """

    def offered(tool) -> bool:
        name = short(tool.tool_name)
        return name in SPANS and name not in WORKFLOW_ONLY_TOOLS

    callable_tools = [tool for tool in tools if offered(tool)]
    withheld = [tool.tool_name for tool in tools if not offered(tool)]
    return callable_tools, withheld


def tools_span(tools: list, took: int, mode: str = "iam") -> dict:
    """The tools/list span, carrying the time measured around ``list_tools_sync``.

    It names every tool the Gateway listed and, separately, the ones withheld from the model.
    """
    how = "bearer-token" if mode == "jwt" else "IAM-signed"
    _, withheld = governed_tools(tools)
    return activity(
        "tool_call",
        "AgentCore Gateway: tools/list",
        f"{len(tools)} MCP tools discovered with {how} requests",
        {
            "category": "gateway",
            "component": "Bedrock AgentCore Gateway",
            "status": "ok",
            "fields": [
                {"label": "endpoint", "value": GATEWAY_URL, "mono": True},
                {"label": "tools", "value": ", ".join(t.tool_name for t in tools)},
                {"label": "withheld_from_model", "value": ", ".join(withheld) or "none"},
                {"label": "policy_engine", "value": POLICY_ENGINE_ID or "none", "mono": True},
            ],
        },
        took,
    )


def drain(queue):
    """Yield the events the hooks queued during a platform-executed tool call."""
    while not queue.empty():
        kind, item = queue.get_nowait()
        if kind == "activity":
            yield {"type": "activity", **item}
        elif kind in ("hold", "booking"):
            yield {"type": kind, **item}
        elif kind == "packages":
            yield {"type": "packages", "packages": item}


async def end_task(task) -> None:
    """Cancel the agent task and wait a moment for it to unwind; never wait for it forever."""
    task.cancel()
    await asyncio.wait({task}, timeout=CANCEL_GRACE_SECONDS)


async def next_event(queue, task, stop, watchdog):
    """The next queued event, a keepalive when one is due, or None when the turn must end.

    The turn ends on a recorded Gateway rejection, or when the idle limit or the cap has
    passed. Every queued item is progress: a model chunk, or a tool call starting or finishing.
    """
    while True:
        if watchdog.expired:
            await end_task(task)
            return None
        if watchdog.keepalive_due():
            return KEEPALIVE_EVENT, None
        wait = min(REJECTION_POLL_SECONDS, watchdog.remaining(), KEEPALIVE_SECONDS)
        try:
            queued = await asyncio.wait_for(queue.get(), wait)
        except TimeoutError:
            if stop.note_rejection():
                await end_task(task)
                return None
            continue
        watchdog.touch()
        return queued


def relay(kind, item):
    """The runtime event for a queued activity, packages, hold or booking; None for other kinds."""
    if kind == "activity":
        return {"type": "activity", **item}
    if kind == "packages":
        return {"type": "packages", "packages": item}
    if kind in ("hold", "booking"):
        return {"type": kind, **item}
    if kind == KEEPALIVE_EVENT:
        return {"type": KEEPALIVE_EVENT}
    return None


async def pump(queue, task, stop, watchdog):
    """Yield runtime events until the agent finishes, a rejection ends it or the watchdog does.

    Returns without an answer when the Gateway rejected a tool call or the watchdog ended the
    turn: Strands can leave a failed call waiting forever, so the Runtime polls the recorded
    status and the clock while it waits.
    """
    answer, usage = "", {}
    while True:
        queued = await next_event(queue, task, stop, watchdog)
        if queued is None:
            return
        kind, item = queued
        event = relay(kind, item)
        if event is not None:
            yield event
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


def confirmed_action(hooks, gateway, turn: TurnContext, hold_target, booking_target, *, watchdog):
    """Place the write the traveler confirmed, if any; return (summary, action, terms).

    The click is the confirmation: the platform places the governed call with the
    exact confirmed terms, and the model narrates what the gateway decided. The call waits
    at most the time the turn has left.
    """

    def call_tool(tool_use_id, name, args):
        watchdog.raise_if_expired()
        return gateway.call_tool_sync(
            tool_use_id=tool_use_id, name=name, arguments=args,
            read_timeout_seconds=timedelta(seconds=watchdog.remaining()))

    if turn.booking_confirmed and booking_target:
        outcome = execute_confirmed_booking(hooks, call_tool, booking_target)
        return outcome, "booking", booking_target
    if turn.hold_confirmed and hold_target:
        return execute_confirmed_hold(hooks, call_tool, hold_target), "hold", hold_target
    return None, "hold", hold_target


class RunningTurn:
    """What one turn's steps share: its context, hooks, watchdog and what it produced."""

    def __init__(self, payload: dict, context: TurnContext, targets: tuple, mode: str,
                 services: tuple) -> None:
        self.payload, self.context, self.mode = payload, context, mode
        self.hold_target, self.booking_target = targets
        self.gateway, self.queue, self.hooks, self.stop, self.watchdog = services
        self.placed: tuple = (None, "hold", self.hold_target)
        self.answer = ""
        self.usage: dict = {}
        self.cut_short = False
        self.history_closed = False


def timed_out(watchdog: TurnWatchdog, hooks: TraceHooks) -> TurnTimedOut:
    """The fixed error for a turn the watchdog ended, naming a write it had placed.

    The same text serves the idle limit and the cap; the cause goes to the log.
    """
    logger.warning("ending the turn: %s", watchdog.reason())
    message = "The concierge did not finish in time. Please try again."
    if hooks.hold:
        message += f" Hold {hooks.hold.get('bookingId')} was placed before the turn ended."
    if hooks.booking:
        message += f" Booking {hooks.booking.get('bookingId')} was confirmed before the turn ended."
    return TurnTimedOut(message)


async def close_history(turn: RunningTurn, agent, session, task) -> None:
    """Close the history an ended turn left open; when it cannot, say so in the log.

    The repair runs on a worker and is recorded under the conversation, so the next turn on it
    waits for the repair (or stops it) before it reads the history. It is never skipped without
    a log line.
    """
    turn.history_closed = True
    conversation = turn.context.conversation_id
    if not task.done():
        logger.warning("the history of session %s was left open: the ended turn's agent task "
                       "is still running", conversation)
        return
    repair = REPAIRS.start(
        conversation, lambda keep_going: settle_history(agent, session, keep_going))
    try:
        await wait_for_job(repair, SETTLE_SECONDS)
    except TimeoutError:
        logger.warning("the history repair of session %s is still running; the next turn "
                       "waits for it", conversation)
    except Exception:  # noqa: BLE001 - the turn's own error is the one to report
        logger.warning("could not close the history of session %s", conversation, exc_info=True)


async def settle(turn: RunningTurn, agent, session, task):
    """Yield what the ended agent task queued, then close the history it left open."""
    for event in drain(turn.queue):
        yield event
    await close_history(turn, agent, session, task)


def make_agent(turn: RunningTurn, tools: list) -> tuple:
    """Build the session manager and the agent; the restore from AgentCore Memory is blocking.

    Returns:
        The session manager and the agent, built together so a cancelled turn can close the
        history the pair holds.
    """
    context = turn.context
    session = memory_manager(context.traveler_id, context.conversation_id)
    agent = build_agent(
        model=concierge_model(),
        tools=tools,
        hooks=[TravelerPinHook(context.traveler_id), turn.hooks, turn.stop],
        phase=4,
        extra_instructions=turn_instructions(
            context.hold_confirmed, turn.hold_target, context.booking_confirmed,
            turn.booking_target
        ),
        session_manager=session,
        callback_handler=None,
    )
    return session, agent


def restored_span(span: dict, session, conversation_id: str) -> dict:
    """The Memory span as the restore left it: failed, with a masked log line, or as measured."""
    if not getattr(session, "restore_failed", False):
        return span
    logger.warning("the conversation history of session %s could not be restored; the turn "
                   "continues without it", conversation_id)
    return restore_failed_span(span)


async def place_confirmed_write(turn: RunningTurn):
    """Place the write the traveler confirmed, if any, and yield the spans it queued.

    The outcome is left in ``turn.placed``. A write that ran out of the turn's time raises.
    """
    try:
        turn.placed = confirmed_action(
            turn.hooks, turn.gateway, turn.context, turn.hold_target, turn.booking_target,
            watchdog=turn.watchdog)
        out_of_time = turn.watchdog.expired
    finally:
        # A platform call that raised still sends the spans of the call it started.
        for event in drain(turn.queue):
            yield event
    if out_of_time:
        raise TurnTimedOut(turn.watchdog.reason())


async def run_agent(turn: RunningTurn, agent, session, prompt: str):
    """Run the agent and yield its events; settle the turn however the caller's stream ends.

    A turn the watchdog or a rejection ended is settled with its events. A caller that closes
    or cancels the stream while the agent runs is settled here too, without events: the agent
    task is cancelled and the history it left open is closed.
    """
    task = asyncio.create_task(drive(agent, prompt, turn.queue))
    try:
        async with aclosing(pump(turn.queue, task, turn.stop, turn.watchdog)) as events:
            async for event in events:
                if event["type"] == "answer":
                    turn.answer, turn.usage = event["text"], event["usage"]
                else:
                    yield event
        if task.cancelled() or not task.done():
            turn.cut_short = True
            async for event in settle(turn, agent, session, task):
                yield event
    finally:
        if not turn.history_closed and (task.cancelled() or not task.done()):
            await end_task(task)
            await close_history(turn, agent, session, task)


async def converse(turn: RunningTurn):
    """List the tools, place a confirmed write, then run the agent; yield the turn's events."""
    context, watchdog = turn.context, turn.watchdog
    listed = time.perf_counter()
    tools = await watchdog.within(OFFLOOP.run(turn.gateway.list_tools_sync))
    yield {"type": "activity", **tools_span(tools, elapsed_ms(listed), turn.mode)}
    callable_tools, _ = governed_tools(tools)
    restore_span = await watchdog.within(
        OFFLOOP.run(memory_span, context.traveler_id, context.conversation_id))
    async for event in place_confirmed_write(turn):
        yield event
    outcome, action, target = turn.placed
    await REPAIRS.finish(context.conversation_id, SETTLE_SECONDS)
    session, agent = await watchdog.within(
        OFFLOOP.run(make_agent, turn, [] if outcome is not None else callable_tools))
    yield {"type": "activity", **restored_span(restore_span, session, context.conversation_id)}
    if outcome is not None:
        prompt = narration_prompt(outcome, target, context.budget_ceiling_cents, action)
    else:
        prompt = turn_prompt(
            str(turn.payload.get("prompt", "")), str(turn.payload.get("memory_context", "")),
            turn.hold_target, context.hold_confirmed)
    async with aclosing(run_agent(turn, agent, session, prompt)) as events:
        async for event in events:
            yield event


def note_sent(watchdog: TurnWatchdog, event: dict) -> None:
    """Record an event sent to the caller; only a keepalive is not progress."""
    watchdog.mark_sent()
    if event["type"] != KEEPALIVE_EVENT:
        watchdog.touch()


async def close_gateway(gateway, exc_info: tuple) -> None:
    """Close the Gateway client off the event loop, waiting a bounded time for it."""
    try:
        await wait_for_job(OFFLOOP.submit(gateway.__exit__, *exc_info), CLOSE_SECONDS)
    except TimeoutError:
        logger.warning("the Gateway client did not close within %g seconds", CLOSE_SECONDS)


def close_late(gateway, started: Future) -> None:
    """Close a Gateway client whose start finished after its turn had given up on it."""
    if started.cancelled() or started.exception() is not None:
        return
    try:
        gateway.__exit__(None, None, None)
    except Exception:  # noqa: BLE001 - nothing is left to report it to
        logger.warning("could not close a Gateway client that started late", exc_info=True)


async def start_gateway(gateway, watchdog: TurnWatchdog) -> None:
    """Start the Gateway MCP client on a worker; a start that outlives the turn is closed."""
    started = OFFLOOP.submit(gateway.__enter__)
    try:
        await watchdog.within(asyncio.wrap_future(started))
    except BaseException:
        started.add_done_callback(lambda done: OFFLOOP.submit(close_late, gateway, done))
        raise


@asynccontextmanager
async def gateway_running(gateway, watchdog: TurnWatchdog):
    """Start the Gateway MCP client under the watchdog, and close it when the turn is over.

    Starting connects and initializes the MCP session, so it runs on a worker: a hung start
    ends the turn at the idle limit instead of blocking the event loop, and a start that
    finishes after the turn was abandoned is closed. An exception raised by the closing
    replaces the turn's own, as it does for ``with gateway``.
    """
    await start_gateway(gateway, watchdog)
    try:
        yield gateway
    except BaseException:
        await close_gateway(gateway, sys.exc_info())
        raise
    await close_gateway(gateway, (None, None, None))


async def run(payload: dict, headers: dict | None = None):
    started = time.monotonic()
    mode = auth_mode()
    proven_traveler, token = caller_from_request(payload, headers, mode)
    watchdog = TurnWatchdog(IDLE_LIMIT_SECONDS, MAX_TURN_SECONDS, KEEPALIVE_SECONDS)
    context, hold_target, booking_target = turn_context(
        payload, proven_traveler, GATEWAY_AUTH_LABELS[mode]
    )
    queue: asyncio.Queue = asyncio.Queue()
    hooks = TraceHooks(queue, context)
    arguments, rejections = recording_arguments(
        gateway_client_arguments(mode, SESSION, REGION, token)
    )
    gateway = MCPClient(url=GATEWAY_URL, **arguments)
    first = {"type": "activity", **start_span(context)}
    note_sent(watchdog, first)
    yield first
    stop = StopOnToolSetupError(rejections)
    turn = RunningTurn(payload, context, (hold_target, booking_target), mode,
                       (gateway, queue, hooks, stop, watchdog))
    try:
        async with gateway_running(gateway, watchdog), aclosing(converse(turn)) as events:
            async for event in events:
                note_sent(watchdog, event)
                yield event
    except RuntimeError as error:
        # A Gateway rejection closes the MCP client, and closing it raises "Connection to the MCP
        # server was closed" over the setup failure that caused it. Report the cause. A turn
        # that passed its watchdog is reported as that, whatever the closing client raised.
        stop.raise_if_stopped()
        if watchdog.expired:
            raise timed_out(watchdog, hooks) from error
        raise
    stop.note_rejection()
    stop.raise_if_stopped()
    if turn.cut_short:
        raise timed_out(watchdog, hooks)
    answer = turn.answer
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
        "booking": hooks.booking,
        "trace_id": trace_id(),
        "usage": turn.usage,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
    }


@app.entrypoint
async def invoke(payload, context=None):
    if payload.get("event") != "concierge_turn":
        yield json.dumps({"type": "error", "message": "Unsupported Meridian Runtime event."})
        return
    headers = getattr(context, "request_headers", None)
    try:
        async with aclosing(run(payload, headers)) as events:
            async for item in events:
                yield json.dumps(item, ensure_ascii=False)
    except CallerRefused as refused:
        yield json.dumps({"type": "error", "code": refused.code,
                          "error_type": type(refused).__name__, "message": str(refused)})
    except Exception as error:  # noqa: BLE001 - the backend renders the failure as a span
        yield json.dumps({"type": "error", "error_type": type(error).__name__,
                          "message": mask_account_ids(str(error))[:600]})


if __name__ == "__main__":
    app.run()
