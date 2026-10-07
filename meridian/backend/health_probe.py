"""Live Aurora reachability probe backing `/api/health`.

`/api/health` used to hardcode `status: "healthy"` and only ever read
startup-time checkpoint state - so it kept reporting healthy on a running
process whose AWS credentials had since expired, while every real Aurora
read (`/api/products`, `/api/packages`, chat) was already failing. This
module runs a real query, the `to_regclass` check for the workflow snapshot table, so
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
SNAPSHOT_TABLE_SQL = "SELECT to_regclass('public.workflow_snapshots') IS NOT NULL AS snapshots"


@dataclass(frozen=True)
class AuroraProbeResult:
    """Outcome of one live Aurora reachability check.

    `component` names what failed: `aurora` when the cluster did not answer,
    `workflow_snapshots` when it answered but the table is missing.

    `error_class` is the exception's type name only (e.g.
    `ExpiredTokenException`). The exception's message text is never
    captured here - it can carry internal detail (ARNs, hostnames) that
    `/api/health` must not expose.
    """

    ok: bool
    error_class: Optional[str] = None
    component: Optional[str] = None


_cache: Optional[AuroraProbeResult] = None
_cache_at: float = 0.0


async def _run_probe() -> AuroraProbeResult:
    """Ask Aurora through the Data API whether the workflow snapshot table exists."""
    try:
        row = await asyncio.wait_for(
            get_rds_data_client().execute_one(SNAPSHOT_TABLE_SQL),
            timeout=PROBE_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # noqa: BLE001 - reports the failure class, never the text.
        return AuroraProbeResult(ok=False, error_class=type(exc).__name__, component="aurora")
    if row and row.get("snapshots") is True:
        return AuroraProbeResult(ok=True)
    return AuroraProbeResult(ok=False, component="workflow_snapshots")


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
