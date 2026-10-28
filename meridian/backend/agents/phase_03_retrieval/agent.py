"""Phase 3: one Strands agent loop over the cumulative tools.

The agent is built from the shared concierge definition, the same one the MeridianConcierge
Runtime builds from. Phase 2's tools arrive over one stdio MCP client per worker; the hybrid
search and the package details run in this process. The agent's own reply is the visible reply.

One limit covers the whole turn the traveler waits for: getting the MCP client and running the
agent. The turn gives the client back without awaiting, so no cancel can skip it.

The limit is 45 seconds because Phase 3 answers the non-streaming ``POST /api/chat`` and the
traveler stops waiting sooner than a longer limit would fire: the browser gives up at 55 seconds
(``REQUEST_TIMEOUT_MS`` in ``frontend/src/api/request.ts``) and the CloudFront API origin read
timeout is 60 seconds (``infra/lib/meridian-web-stack.ts``). At 45 seconds the fixed error and
the pool reset reach the traveler with 10 seconds to spare. A typical turn takes 4 to 6 seconds.
``tests/test_phase3_agent.py`` fails if the limit is not below both.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, List

from backend.activity import ActivityEntry, Product, TraceTelemetry, create_activity
from backend.agents.concierge_definition import TravelerPinHook, build_agent
from backend.agents.phase_03_retrieval.hooks import AGENT_FILE, AGENT_NAME, RetrievalTraceHooks
from backend.agents.phase_03_retrieval.mcp_session import concierge_pool
from backend.agents.phase_03_retrieval.tools import local_tools
from backend.agents.strands_hooks import StopOnToolSetupError, ToolSetupError
from backend.bedrock_model import bedrock_model
from backend.catalog_compat import row_to_api_product
from backend.config import config
from backend.security.redaction import masked_traceback
from backend.timing import clock, elapsed_ms

logger = logging.getLogger(__name__)

TURN_TIMEOUT_SECONDS = 45
# Acquires wait on these threads rather than the event loop's default executor, so a turn that
# is cancelled while it waits still gets a handle on the acquire and can give its hold back.
_ACQUIRING = ThreadPoolExecutor(thread_name_prefix="meridian-concierge-acquire")


class RetrievalTurnError(RuntimeError):
    """The turn failed; the message is safe to show and names no internals."""


@dataclass(frozen=True)
class RetrievalTurn:
    """What one Phase 3 turn produced."""

    message: str
    products: List[Product]
    activities: List[ActivityEntry]
    tools_called: List[str]
    model_id: str
    stop_reason: str = ""


def _activity(title: str, details: str, model_id: str, elapsed: int | None = None) -> ActivityEntry:
    entry = create_activity(
        "reasoning", title=title, details=details, execution_time_ms=elapsed,
        agent_name=AGENT_NAME, agent_file=AGENT_FILE)
    entry.telemetry = TraceTelemetry(
        category="model", component="Strands agent on Amazon Bedrock", status="ok",
        fields=[{"label": "model", "value": model_id, "mono": True}])
    return entry


def _products(rows: List[dict], limit: int) -> List[Product]:
    """The cards for the first ``limit`` rows, with the rerank fields the UI animates from.

    ``row_to_api_product`` serves callers whose rows were never reranked, so the three rerank
    fields are mapped here, as ``retrieval.hybrid._product`` does for Phase 5.
    """
    return [
        Product(
            **row_to_api_product(row),
            pre_rerank_position=row.get("pre_rerank_position"),
            pre_rerank_similarity=row.get("pre_rerank_similarity"),
            rank_delta=row.get("rank_delta"),
        )
        for row in rows[:limit]
    ]


def _give_back_unclaimed(pool: Any) -> Callable[[Future], None]:
    """Release the hold an acquire took for a turn that was cancelled before it arrived."""

    def give_back(acquired: Future) -> None:
        if not acquired.cancelled() and acquired.exception() is None:
            pool.release(acquired.result()[0])

    return give_back


async def _acquire(pool: Any) -> tuple[int, list]:
    """The pool's current generation and its tools, or a turn error that says what failed."""
    pending = _ACQUIRING.submit(pool.acquire)
    try:
        return await asyncio.wrap_future(pending)
    except asyncio.CancelledError:
        pending.add_done_callback(_give_back_unclaimed(pool))
        raise
    except Exception as error:
        logger.error("meridian-concierge MCP tools did not start (%s)\n%s",
                     type(error).__name__, masked_traceback(error))
        raise RetrievalTurnError(
            "The meridian-concierge tools did not start; the backend log has the details."
        ) from error


async def _invoke(agent: Any, stop: StopOnToolSetupError, query: str) -> Any:
    """Run the agent once; any failure becomes a turn error whose words are safe to show.

    The stop hook ends the turn after a tool that could not be set up, with a fixed reply;
    ``raise_if_stopped`` turns that into the error, so a setup failure is never shown as an
    answer.
    """
    try:
        result = await agent.invoke_async(f"Traveler request:\n{query}")
        stop.raise_if_stopped()
    except ToolSetupError as error:
        logger.error("A Phase 3 tool could not be set up (%s)\n%s",
                     type(error).__name__, masked_traceback(error))
        raise RetrievalTurnError(
            "A tool this turn needs could not be set up; the backend log has the details."
        ) from error
    except Exception as error:
        logger.error("Phase 3 agent turn failed (%s)\n%s",
                     type(error).__name__, masked_traceback(error))
        raise RetrievalTurnError(
            "The retrieval agent could not complete this turn; the backend log has the details."
        ) from error
    return result


class _Turn:
    """One turn's hold on the MCP client pool, its trace and its timing."""

    def __init__(self, pool: Any, model: Any) -> None:
        self.pool, self.model = pool, model
        self.model_id = str(model.get_config().get("model_id", ""))
        self.spans: List[ActivityEntry] = []
        self.trace = RetrievalTraceHooks(self.spans)
        self.generation: int | None = None
        self.timed_out = False
        self.reply_started = clock()

    async def run(self, query: str, traveler_id: str) -> Any:
        """Get the client and run the agent, both under ``TURN_TIMEOUT_SECONDS``."""
        limit = asyncio.timeout(TURN_TIMEOUT_SECONDS)
        try:
            async with limit:
                return await self._run(query, traveler_id)
        except TimeoutError as error:
            if not limit.expired():
                raise
            self.timed_out = True
            raise RetrievalTurnError(
                f"The retrieval agent did not finish within {TURN_TIMEOUT_SECONDS} seconds."
            ) from error

    async def _run(self, query: str, traveler_id: str) -> Any:
        started = clock()
        self.generation, mcp_tools = await _acquire(self.pool)
        stop = StopOnToolSetupError(self.trace.mcp_failures)
        tools = [*mcp_tools, *local_tools(self.spans)]
        self.spans.append(_activity(
            "Retrieval agent: turn started",
            f"{self.model_id} chooses among {len(tools)} tools: "
            + ", ".join(sorted(tool.tool_name for tool in tools)),
            self.model_id, elapsed_ms(started)))
        agent = build_agent(
            model=self.model, tools=tools, phase=3, callback_handler=None,
            hooks=[TravelerPinHook(traveler_id), self.trace, stop])
        self.reply_started = clock()
        return await _invoke(agent, stop, query)

    def give_back(self) -> None:
        """Return the hold without awaiting, so no cancel can skip it and no start delays it.

        Only a suspect client is retired. The turn timed out, or an MCP tool's error result was
        built by the client (``Tool execution failed`` or ``cancelled locally``: the session or
        the server process is gone), has a shape nobody recognizes (fail toward recovery), or
        names a credential, permission or configuration failure (expired credentials need a
        fresh server process).
        An error the server itself reported (``Error executing tool ...``, argument validation
        included) means the server answered, so its session is alive and the client is released.
        A model failure, a cancel or a local tool's error also leaves the client usable.
        """
        if self.generation is None:
            return
        if self.timed_out or self.trace.client_suspect:
            self.pool.reset(self.generation)
        else:
            self.pool.release(self.generation)


async def run_retrieval_turn(
    query: str,
    *,
    traveler_id: str,
    limit: int = 5,
    model: Any = None,
    pool: Any = None,
) -> RetrievalTurn:
    """Run one Phase 3 turn.

    Args:
        query: the traveler's words.
        traveler_id: the authorized traveler; pinned on every tool that takes one.
        limit: most products to return.
        model: a Strands model; defaults to the configured Bedrock model.
        pool: the MCP client pool; defaults to the process-wide pool.

    Raises:
        RetrievalTurnError: The tools did not start, a tool could not be set up, the turn
            failed or timed out, or the model wrote no reply. A timed-out turn, or one whose
            MCP tool call failed on the client side, retires its MCP client (``pool.reset``);
            every other turn gives it back (``pool.release``), and other turns keep theirs.
    """
    turn = _Turn(pool or concierge_pool(), model or bedrock_model(config.bedrock.model_id))
    try:
        result = await turn.run(query, traveler_id)
    finally:
        turn.give_back()
    message = str(result).strip()
    if not message:
        raise RetrievalTurnError("The retrieval agent wrote no reply.")
    stop_reason = str(getattr(result, "stop_reason", "") or "")
    usage = dict(getattr(getattr(result, "metrics", None), "accumulated_usage", {}) or {})
    turn.spans.append(_activity(
        "Retrieval agent: reply written",
        f"{len(turn.trace.tools_called)} tool calls; input {usage.get('inputTokens', 0)} tokens, "
        f"output {usage.get('outputTokens', 0)} tokens; stop reason {stop_reason or 'unknown'}",
        turn.model_id, elapsed_ms(turn.reply_started)))
    return RetrievalTurn(
        message=message, products=_products(turn.trace.packages, limit), activities=turn.spans,
        tools_called=turn.trace.tools_called, model_id=turn.model_id, stop_reason=stop_reason)
