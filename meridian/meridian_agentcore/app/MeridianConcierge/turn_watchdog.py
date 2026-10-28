"""An idle watchdog and a hard cap over a Phase 4 turn, and the history an ended turn leaves.

Strands 1.57.2 never returns from an MCP tool call once the transport failed while a cancel
signal was passed, so nothing inside the turn ends it. The watchdog does: the turn ends when
nothing has happened for the idle limit, or when it has run for the hard cap.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")
logger = logging.getLogger(__name__)

INTERRUPTED_RESULT = "This tool call was stopped before it finished."
INTERRUPTED_REPLY = "That request did not finish, so I stopped before answering."
KEEPALIVE_EVENT = "keepalive"


class TurnTimedOut(RuntimeError):
    """The turn went idle or ran past its cap; the message is safe to show."""


class TurnWatchdog:
    """Ends a turn that makes no progress for ``idle_seconds`` or runs past ``max_seconds``.

    Progress is any event the Runtime streams to its caller and any model or tool event it
    observes; callers report it with ``touch``. A keepalive is sent to the caller but is not
    progress, so it never keeps a stuck turn alive. ``mark_sent`` records what the caller was
    sent last, so a long stretch of progress the caller cannot see gets a keepalive.
    """

    def __init__(self, idle_seconds: float, max_seconds: float, keepalive_seconds: float) -> None:
        self.idle_seconds = idle_seconds
        self.max_seconds = max_seconds
        self.keepalive_seconds = keepalive_seconds
        now = time.monotonic()
        self.started_at = self.touched_at = self.sent_at = now
        self.ended_for: str | None = None

    def touch(self) -> None:
        """Record progress."""
        self.touched_at = time.monotonic()

    def mark_sent(self) -> None:
        """Record that the caller was just sent an event."""
        self.sent_at = time.monotonic()

    def remaining(self) -> float:
        """Seconds until the idle limit or the cap ends the turn, whichever is first."""
        now = time.monotonic()
        idle_left = self.touched_at + self.idle_seconds - now
        cap_left = self.started_at + self.max_seconds - now
        return max(0.0, min(idle_left, cap_left))

    @property
    def expired(self) -> bool:
        """True once the turn is over its idle limit or its cap; once true, always true."""
        if self.ended_for is None and self.remaining() <= 0.0:
            capped = time.monotonic() - self.started_at >= self.max_seconds
            self.ended_for = (f"The turn ran past its {self.max_seconds:g} second cap." if capped
                              else f"The turn made no progress for {self.idle_seconds:g} seconds.")
        return self.ended_for is not None

    def keepalive_due(self) -> bool:
        """True when the caller has been sent nothing for the keepalive interval."""
        return time.monotonic() - self.sent_at >= self.keepalive_seconds

    def raise_if_expired(self) -> None:
        """Raise ``TurnTimedOut`` once the idle limit or the cap has passed."""
        if self.expired:
            raise TurnTimedOut(self.reason())

    def reason(self) -> str:
        """Why the turn ended, for logs; the traveler gets a fixed message instead."""
        return self.ended_for or "The turn has not ended."

    async def within(self, awaitable: Awaitable[T]) -> T:
        """Await ``awaitable`` until it finishes or the watchdog ends the turn.

        Progress made elsewhere while it waits extends the wait, up to the cap. Work started
        with ``asyncio.to_thread`` cannot be stopped, so an expired wait abandons its thread.

        Raises:
            TurnTimedOut: The idle limit or the cap passed first.
        """
        future = asyncio.ensure_future(awaitable)
        try:
            while True:
                done, _ = await asyncio.wait({future}, timeout=self.remaining())
                if done:
                    return future.result()
                if self.expired:
                    raise TurnTimedOut(self.reason())
        finally:
            if not future.done():
                future.cancel()


def open_tool_use_ids(messages: list[dict]) -> list[str]:
    """The ids of the toolUse blocks in the last message when an assistant sent it."""
    if not messages or messages[-1].get("role") != "assistant":
        return []
    return [block["toolUse"]["toolUseId"] for block in messages[-1].get("content", [])
            if isinstance(block, dict) and "toolUse" in block]


def settle_history(
    agent: Any, session: Any, keep_going: Callable[[], bool] = lambda: True
) -> int:
    """End a cancelled turn's history on an assistant message, with every toolUse answered.

    AgentCore Memory saves each message as the agent appends it, so a turn cancelled during a
    tool call leaves a toolUse with no toolResult, or a user message with no reply, and the
    traveler's next turn would send Bedrock a history it rejects. This appends, and saves
    through the session manager, a toolResult with a fixed error for each open toolUse and then
    a fixed assistant reply. The saved history then alternates roles and pairs every tool call.

    A save that was running when the turn was cancelled keeps running on its thread and can land
    after this repair. That is safe without waiting for it: the session manager stamps each event
    with the ``created_at`` of its message, taken when the save started, and keeps its stamps
    increasing, so the late message sorts before the closing messages written here, and this
    repair never writes a message the agent already holds. A restore between the two sees the
    closing messages without the late one, a history Strands prunes before the model sees it.

    Args:
        agent: The Strands agent whose task was cancelled and has finished.
        session: Its session manager, or None.
        keep_going: Checked before each message is appended; False stops the repair there, so a
            repair that outlived its turn never appends after the next turn has begun.

    Returns:
        The number of messages appended.
    """
    messages = agent.messages
    if not messages:
        return 0
    open_ids = open_tool_use_ids(messages)
    if messages[-1].get("role") == "assistant" and not open_ids:
        return 0
    added: list[dict] = []
    if open_ids:
        added.append({"role": "user", "content": [
            {"toolResult": {"toolUseId": tool_use_id, "status": "error",
                            "content": [{"text": INTERRUPTED_RESULT}]}}
            for tool_use_id in open_ids]})
    added.append({"role": "assistant", "content": [{"text": INTERRUPTED_REPLY}]})
    for count, message in enumerate(added):
        if not keep_going():
            logger.warning("the history repair stopped after %d of %d messages", count, len(added))
            return count
        agent.messages.append(message)
        if session is not None:
            session.append_message(message, agent)
    return len(added)
