"""One started stdio client for the meridian-concierge server per process.

Phase 2 spawns a server per turn. Phase 3 serves many turns, so the client starts once and is
shared by every turn of the worker. A turn takes the current client with ``acquire`` and gives
it back with ``release``, or with ``reset`` when the client is suspect. A reset retires that
client: the next turn starts a fresh one, and the retired client stops when the last turn that
holds it gives it back, so one traveler's failed turn never stops a client another traveler's
turn is still using. The old per-turn client wedged a cached stdio session once; a reset is how
this one recovers.

A start runs on its own thread, never under the pool's lock. Every turn that needs the same
generation waits on that one start, and only until its deadline; a start that misses it is
abandoned and its client stopped. ``release`` and ``reset`` only change counts under the lock
and stop a retired client on another thread, so they never wait on a start or a stop.

An abandoned start must not leave its server running. Strands gives its MCP session no read
timeout, so a server that never answers ``initialize`` would hold the client's thread, and with
it the server process, for good. The transport the pool hands Strands therefore ends the
session's read stream when the server's first message has not come within the start timeout.
``initialize`` then fails, the transport exits and ``stdio_client`` terminates the child.
"""

from __future__ import annotations

import atexit
import logging
import threading
import time
from concurrent.futures import Future
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Dict, List, NamedTuple, Optional, Tuple

import anyio
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from strands.tools.mcp import MCPClient

from backend.agents.concierge_definition import PHASE3_TOOLS
from backend.agents.phase_03_retrieval.tools import LOCAL_TOOLS
from backend.mcp.concierge_mcp_client import concierge_server_parameters

logger = logging.getLogger(__name__)

MCP_TOOLS = tuple(name for name in PHASE3_TOOLS if name not in LOCAL_TOOLS)
# Covers the whole start: spawning the server, the MCP handshake and the tools/list call.
MCP_START_TIMEOUT_SECONDS = 30


class _FirstMessageBound:
    """A read stream that ends if the server sends nothing within ``timeout`` seconds.

    Only the first message is bounded; every later read waits as long as the session does.
    """

    def __init__(self, stream: Any, timeout: float) -> None:
        self._stream, self._timeout, self._heard = stream, timeout, False

    async def __aenter__(self) -> "_FirstMessageBound":
        await self._stream.__aenter__()
        return self

    async def __aexit__(self, *exc_info: Any) -> Any:
        return await self._stream.__aexit__(*exc_info)

    def __aiter__(self) -> "_FirstMessageBound":
        return self

    async def __anext__(self) -> Any:
        if self._heard:
            return await self._stream.__anext__()
        with anyio.move_on_after(self._timeout) as silence:
            message = await self._stream.__anext__()
        if silence.cancelled_caught:
            raise StopAsyncIteration
        self._heard = True
        return message


@asynccontextmanager
async def _bounded_stdio(
    parameters: StdioServerParameters, first_message_timeout: float
) -> AsyncIterator[tuple]:
    async with stdio_client(parameters) as (read_stream, write_stream, *rest):
        yield (_FirstMessageBound(read_stream, first_message_timeout), write_stream, *rest)


def new_client(
    parameters: Optional[StdioServerParameters] = None,
    *,
    start_timeout: float = MCP_START_TIMEOUT_SECONDS,
) -> MCPClient:
    """A client for the concierge server (or ``parameters``) that cannot outlive a silent server."""
    return MCPClient(
        lambda: _bounded_stdio(parameters or concierge_server_parameters(), start_timeout),
        startup_timeout=start_timeout)


def _run_detached(work: Callable[..., None], *args: Any) -> None:
    """Run ``work`` on its own daemon thread; nothing waits for it."""
    threading.Thread(target=work, args=args, daemon=True).start()


class _Start(NamedTuple):
    """One server start, shared by every turn that waits for its generation."""

    client: Any
    done: Future
    deadline: float


class ConciergeMcpPool:
    """A lazily started MCP client shared by the turns of one worker process."""

    def __init__(
        self,
        factory: Callable[[], Any] = new_client,
        *,
        start_timeout: float = MCP_START_TIMEOUT_SECONDS,
    ) -> None:
        self._factory = factory
        self._start_timeout = start_timeout
        self._lock = threading.Lock()
        self._generation = 0
        self._clients: Dict[int, Any] = {}
        self._holders: Dict[int, int] = {}
        self._tools: List[Any] = []
        self._starts: Dict[int, _Start] = {}
        self._exit_registered = False

    def acquire(self) -> Tuple[int, List[Any]]:
        """The current client's generation and the server's tools, starting a client if needed.

        Raises:
            RuntimeError: The server offers a different tool set than Phase 3 expects, or the
                pool closed during the start.
            TimeoutError: The start did not finish within the start timeout.
        """
        while True:
            with self._lock:
                generation = self._generation
                if generation in self._clients:
                    self._holders[generation] = self._holders.get(generation, 0) + 1
                    return generation, list(self._tools)
                start = self._starts.get(generation) or self._begin(generation)
            self._wait(generation, start)

    def release(self, generation: int) -> None:
        """A turn is done with the client of ``generation``."""
        self._give_back(generation, retire=False)

    def reset(self, generation: int) -> None:
        """A turn found the client of ``generation`` suspect: retire it once no turn holds it."""
        self._give_back(generation, retire=True)

    def close(self) -> None:
        """Stop every client, held, idle or starting; for process exit and tests."""
        with self._lock:
            clients = [*self._clients.values(), *(s.client for s in self._starts.values())]
            for start in self._starts.values():
                _settle(start.done, RuntimeError("the meridian-concierge MCP pool was closed"))
            self._clients.clear()
            self._holders.clear()
            self._starts.clear()
            self._generation += 1
        for client in clients:
            self._stop(client)

    def _give_back(self, generation: int, *, retire: bool) -> None:
        with self._lock:
            self._holders[generation] = self._holders.get(generation, 1) - 1
            if retire and generation == self._generation:
                self._generation += 1
            client = None
            if generation != self._generation and self._holders[generation] <= 0:
                client = self._clients.pop(generation, None)
                self._holders.pop(generation, None)
        if client is not None:
            _run_detached(self._stop, client)

    def _begin(self, generation: int) -> _Start:
        """Start a client for ``generation`` on its own thread. The caller holds the lock."""
        start = _Start(self._factory(), Future(), time.monotonic() + self._start_timeout)
        self._starts[generation] = start
        threading.Thread(target=self._run_start, args=(generation, start), daemon=True).start()
        if not self._exit_registered:
            atexit.register(self.close)
            self._exit_registered = True
        return start

    def _wait(self, generation: int, start: _Start) -> None:
        """Wait for a shared start until its deadline, then abandon it."""
        try:
            start.done.result(timeout=max(0.0, start.deadline - time.monotonic()))
            return
        except TimeoutError:
            if start.done.done():
                start.done.result()
                return
        error = TimeoutError(
            f"the meridian-concierge server did not start within {self._start_timeout} seconds")
        if self._claim(generation, start, error):
            _run_detached(self._stop, start.client)
        raise error

    def _run_start(self, generation: int, start: _Start) -> None:
        """Start the client. A failed start is stopped before its waiters hear of it."""
        try:
            start.client.start()
            tools = start.client.list_tools_sync()
            self._check(sorted(tool.tool_name for tool in tools))
        except BaseException as error:
            with self._lock:
                owned = self._starts.get(generation) is start
            if owned:
                self._stop(start.client)
            self._claim(generation, start, error)
            return
        with self._lock:
            if self._starts.get(generation) is start:
                del self._starts[generation]
                self._clients[generation], self._tools = start.client, tools
            _settle(start.done, None)

    def _claim(self, generation: int, start: _Start, error: BaseException) -> bool:
        """End a start with ``error``; True when this caller took it, and so stops its client."""
        with self._lock:
            owned = self._starts.get(generation) is start
            if owned:
                del self._starts[generation]
            _settle(start.done, error)
        return owned

    @staticmethod
    def _check(offered: List[str]) -> None:
        expected = sorted(MCP_TOOLS)
        unexpected = sorted(set(offered) - set(expected))
        missing = sorted(set(expected) - set(offered))
        if unexpected or missing:
            raise RuntimeError(
                "the meridian-concierge server offers a different tool set than Phase 3 expects: "
                f"unexpected {unexpected or 'none'}, missing {missing or 'none'}; "
                "backend/mcp/concierge_server.py and MCP_TOOLS must agree"
            )

    @staticmethod
    def _stop(client: Any) -> None:
        try:
            client.stop(None, None, None)
        except Exception as error:  # noqa: BLE001 - a client that will not stop is already gone
            logger.warning("meridian-concierge MCP client did not stop cleanly: %s",
                           type(error).__name__)


def _settle(done: Future, error: Optional[BaseException]) -> None:
    """Resolve a start's future once; the caller holds the pool's lock."""
    if done.done():
        return
    if error is None:
        done.set_result(None)
    else:
        done.set_exception(error)


_pool: ConciergeMcpPool | None = None
_pool_lock = threading.Lock()


def concierge_pool() -> ConciergeMcpPool:
    """The process-wide pool."""
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ConciergeMcpPool()
        return _pool
