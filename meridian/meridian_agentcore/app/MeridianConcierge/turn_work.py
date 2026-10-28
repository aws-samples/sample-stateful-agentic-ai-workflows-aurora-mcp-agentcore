"""Work a turn hands to threads, and the order of the history repairs it leaves behind.

Setup and teardown calls (the agent build, the Gateway client start and close, the history
repair) block in libraries that offer no way to stop them. They run on their own small executor,
not asyncio's default one, so a call that hangs holds one of these workers and nothing else:
``asyncio.to_thread`` work elsewhere in the process keeps its threads. A turn that gives up on
such a call abandons the thread; the executor counts the busy workers and logs when it is full.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

logger = logging.getLogger(__name__)


class OffLoopExecutor:
    """A bounded thread pool that says so when every worker is busy."""

    def __init__(self, workers: int, name: str = "concierge-offloop") -> None:
        self.workers = workers
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=name)
        self._lock = threading.Lock()
        self._busy = 0

    @property
    def busy(self) -> int:
        """Calls running or waiting for a worker."""
        with self._lock:
            return self._busy

    def submit(self, work: Callable[..., Any], *args: Any) -> Future:
        """Start ``work(*args)`` on a worker; a full pool queues it and logs a warning."""
        with self._lock:
            self._busy += 1
            waiting = self._busy - self.workers
        if waiting > 0:
            logger.warning(
                "all %d off-loop workers are busy, %d call(s) are waiting; a call that never "
                "returns holds its worker until its library gives up", self.workers, waiting)
        try:
            future = self._pool.submit(work, *args)
        except BaseException:
            self._finished()
            raise
        future.add_done_callback(lambda _future: self._finished())
        return future

    async def run(self, work: Callable[..., Any], *args: Any) -> Any:
        """Await ``work(*args)`` on a worker. Cancelling the wait leaves a running call running."""
        return await asyncio.wrap_future(self.submit(work, *args))

    def _finished(self) -> None:
        with self._lock:
            self._busy -= 1


class HistoryRepairs:
    """Orders each conversation's history repair before the next turn's first read or write.

    A repair appends closing messages to the saved history. One that outlives its turn's wait
    could append after the traveler's next turn has started and put them out of order. The next
    turn on the same conversation therefore calls ``finish`` before it reads the history: it
    waits a moment for the repair, and if the repair is still running it tells the repair to
    stop before its next append and records that the history was left open.
    """

    def __init__(self, executor: OffLoopExecutor) -> None:
        self._executor = executor
        self._open: dict[str, tuple[Future, threading.Event]] = {}
        self._lock = threading.Lock()

    def start(self, conversation_id: str, repair: Callable[[Callable[[], bool]], int]) -> Future:
        """Run ``repair(keep_going)`` on a worker; ``keep_going()`` is False once told to stop.

        A repair still running for the same conversation is told to stop first, so two repairs
        never append to one history, and the registry always holds the newest.
        """
        stop = threading.Event()
        future = self._executor.submit(repair, lambda: not stop.is_set())
        with self._lock:
            earlier = self._open.get(conversation_id)
            self._open[conversation_id] = (future, stop)
        if earlier is not None:
            earlier[1].set()
            logger.warning("a history repair of session %s was told to stop: a newer repair "
                           "replaced it", conversation_id)
        future.add_done_callback(lambda done: self._forget(conversation_id, done))
        return future

    def _forget(self, conversation_id: str, future: Future) -> None:
        with self._lock:
            entry = self._open.get(conversation_id)
            if entry is not None and entry[0] is future:
                del self._open[conversation_id]

    def running(self, conversation_id: str) -> bool:
        """True while a repair for the conversation has not finished."""
        with self._lock:
            return conversation_id in self._open

    async def finish(self, conversation_id: str, seconds: float) -> bool:
        """Wait up to ``seconds`` for the conversation's repair; stop it if it is still running.

        Returns:
            True when no repair was left running; False when one was told to stop, which means
            the saved history may have been left open.
        """
        with self._lock:
            entry = self._open.get(conversation_id)
        if entry is None:
            return True
        future, stop = entry
        done, _ = await asyncio.wait({asyncio.wrap_future(future)}, timeout=seconds)
        if done:
            return True
        stop.set()
        logger.warning(
            "the history repair of session %s was still running after %g seconds and was told "
            "to stop; its history may have been left open", conversation_id, seconds)
        return False
