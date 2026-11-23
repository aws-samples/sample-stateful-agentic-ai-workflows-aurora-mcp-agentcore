"""Where a long model turn runs, and how many may run at once.

A Concierge or Workflow turn holds a thread for up to about 110 seconds while it waits on the
AgentCore Runtime. The default executor (``asyncio.to_thread``) holds ``min(32, cpus + 4)``
threads, 5 on the 1 vCPU App Runner service, and sign-in verification and every Data API call
share it. A handful of turns could therefore keep a traveler from signing in. Runtime calls run
on their own pool instead, so they can never take a thread from those.

``turn_slot`` is a cap over the whole process, never per traveler: the presenter, the warm-up,
the identity proof and the test harness all sign in as the same traveler. A turn beyond the cap
gets a 429 with a JSON error. The counters are touched only on the event loop, so they need no
lock.
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

from fastapi import HTTPException

# 16 matches the most the load-test tool puts in flight (`MAX_CONCURRENCY`), is far above what one
# presenter, the warm-up and the identity proof use together, and holds 16 slow turns without
# touching the default pool's 5 threads.
RUNTIME_TURN_WORKERS = 16
# Equal to the pool, so an admitted turn never queues behind another for a worker. Raise both
# together.
MAX_CONCURRENT_TURNS = 16
RUNTIME_THREAD_PREFIX = "meridian-runtime-turn"
TURNS_BUSY_CODE = "turns_busy"
RETRY_AFTER_SECONDS = "10"

_pool = ThreadPoolExecutor(
    max_workers=RUNTIME_TURN_WORKERS, thread_name_prefix=RUNTIME_THREAD_PREFIX,
)
_in_flight = 0


class TurnsBusy(HTTPException):
    """The 429 for a turn that arrives while every slot is taken."""

    code = TURNS_BUSY_CODE

    def __init__(self) -> None:
        super().__init__(
            status_code=429,
            detail="The concierge is handling as many conversations as it can. "
                   "Try again in a few seconds.",
            headers={"Retry-After": RETRY_AFTER_SECONDS},
        )


def refuse_if_full() -> None:
    """Raise ``TurnsBusy`` when no slot is free, without taking one.

    The stream routes call this before they send headers, so a refusal is a plain 429. The slot
    itself is taken later, by the work, in ``turn_slot``.
    """
    if _in_flight >= MAX_CONCURRENT_TURNS:
        raise TurnsBusy()


@asynccontextmanager
async def turn_slot() -> AsyncIterator[None]:
    """Hold one of ``MAX_CONCURRENT_TURNS`` slots for the length of a model turn."""
    global _in_flight
    refuse_if_full()
    _in_flight += 1
    try:
        yield
    finally:
        _in_flight -= 1


async def run_runtime_call(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Run a blocking Runtime call on the Runtime pool, in a copy of the caller's context.

    ``asyncio.to_thread`` copies the context, which carries the caller's token and the request's
    event sink; ``run_in_executor`` does not, so the copy is made here.
    """
    context = contextvars.copy_context()
    call = functools.partial(context.run, func, *args, **kwargs)
    return await asyncio.get_running_loop().run_in_executor(_pool, call)
