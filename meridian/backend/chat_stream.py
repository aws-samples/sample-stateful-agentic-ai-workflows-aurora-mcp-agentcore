"""Request-scoped progress transport, including callbacks from asyncio.to_thread.

Events are provisional. Only the router's final response confirms a completed turn.
The sink is isolated by ContextVar and never changes authorization or tool behavior.
"""
from contextvars import ContextVar
from typing import Callable

chat_event_sink: ContextVar[Callable[[dict], None] | None] = ContextVar("chat_event_sink", default=None)


def emit_chat_event(event: dict) -> None:
    sink = chat_event_sink.get()
    if sink is not None:
        sink(event)
