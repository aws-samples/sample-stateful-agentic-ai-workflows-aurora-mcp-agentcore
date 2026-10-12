"""Span timing: whole milliseconds measured around the real call, never estimated.

Every trace span that shows a duration takes it from ``clock()`` read before the
call and ``elapsed_ms()`` read after it. Tests replace ``_clock`` to prove a
duration was measured around the call it labels.
"""

from __future__ import annotations

import time

_clock = time.perf_counter


def clock() -> float:
    """Read the span clock before the call being measured."""
    return _clock()


def elapsed_ms(started: float) -> int:
    """Whole milliseconds since ``started``, a reading from ``clock()``."""
    return round((_clock() - started) * 1000)
