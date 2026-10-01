"""Live Aurora reachability probe backing `/api/health`.

`/api/health` used to hardcode `status: "healthy"` and only ever read
startup-time checkpoint state - so it kept reporting healthy on a running
process whose AWS credentials had since expired, while every real Aurora
read (`/api/products`, `/api/packages`, chat) was already failing. This
module runs the same real `SELECT 1` any other Aurora read would run, so
the endpoint reflects what is true right now, not what was true at
startup. The result is cached briefly so a presenter (or a poller)
refreshing `/api/health` does not fire a fresh Data API call on every
request; the underlying check is always live.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Optional

from backend.db.rds_data_client import get_rds_data_client

PROBE_TIMEOUT_SECONDS = 2.0
CACHE_TTL_SECONDS = 10.0


@dataclass(frozen=True)
class AuroraProbeResult:
    """Outcome of one live Aurora reachability check.

    `error_class` is the exception's type name only (e.g.
    `ExpiredTokenException`). The exception's message text is never
    captured here - it can carry internal detail (ARNs, hostnames) that
    `/api/health` must not expose.
    """

    ok: bool
    error_class: Optional[str] = None


_cache: Optional[AuroraProbeResult] = None
_cache_at: float = 0.0


async def _run_probe() -> AuroraProbeResult:
    """Run one real `SELECT 1` against Aurora through the Data API."""
    try:
        await asyncio.wait_for(
            get_rds_data_client().execute_one("SELECT 1"),
            timeout=PROBE_TIMEOUT_SECONDS,
        )
        return AuroraProbeResult(ok=True)
    except Exception as exc:  # noqa: BLE001 - reports the failure class, never the text.
        return AuroraProbeResult(ok=False, error_class=type(exc).__name__)


async def probe_aurora() -> AuroraProbeResult:
    """Return a live Aurora reachability result, cached for at most 10s.

    Returns:
        The most recent real probe result. A cache hit still reflects a
        real check performed within the last `CACHE_TTL_SECONDS`; it is
        never a value invented for the occasion.
    """
    global _cache, _cache_at
    now = time.monotonic()
    if _cache is not None and (now - _cache_at) < CACHE_TTL_SECONDS:
        return _cache
    result = await _run_probe()
    _cache = result
    _cache_at = now
    return result


def reset_probe_cache() -> None:
    """Clear the cached probe result so the next call re-probes. Test-only."""
    global _cache, _cache_at
    _cache = None
    _cache_at = 0.0
