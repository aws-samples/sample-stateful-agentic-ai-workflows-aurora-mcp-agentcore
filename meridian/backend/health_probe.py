"""Live Aurora reachability probe backing `/api/health`.

`/api/health` used to hardcode `status: "healthy"` and only ever read
startup-time snapshot state - so it kept reporting healthy on a running
process whose AWS credentials had since expired, while every real Aurora
read (`/api/products`, `/api/packages`, chat) was already failing. This
module runs a real query, the `to_regclass` check for the workflow snapshot table, so
the endpoint reflects what is true right now, not what was true at
startup. The result is cached briefly so a presenter (or a poller)
refreshing `/api/health` does not fire a fresh Data API call on every
request; the underlying check is always live.

A Data API call cannot be abandoned: its thread runs to the end and the shield in
`run_blocking_shielded` keeps awaiting it, so a timeout on the await would not return. The probe
therefore runs as its own task, one at a time, and a caller waits for it only up to
`PROBE_TIMEOUT_SECONDS`. A caller that finds a probe still running, or whose wait ran out, gets
the last result marked `stale` (while it is younger than `STALE_LIMIT_SECONDS`) or an
`aurora` failure of class `TimeoutError`. The probe that finishes late becomes the next answer.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, replace
from typing import Optional

from backend.db.rds_data_client import get_rds_data_client

PROBE_TIMEOUT_SECONDS = 2.0
CACHE_TTL_SECONDS = 10.0
# A last result older than this says nothing about now, so it is not served as a stale answer.
STALE_LIMIT_SECONDS = 30.0
WORKFLOW_TABLES_SQL = """
SELECT to_regclass('public.workflow_snapshots') IS NOT NULL AS snapshots,
       to_regclass('public.workflow_session_stops') IS NOT NULL AS stops,
       current_user AS db_user
"""


@dataclass(frozen=True)
class AuroraProbeResult:
    """Outcome of one live Aurora reachability check.

    `component` names what failed: `aurora` when the cluster did not answer,
    `workflow_snapshots` or `workflow_session_stops` when it answered but that
    table is missing.

    `error_class` is the exception's type name only (e.g.
    `ExpiredTokenException`). The exception's message text is never
    captured here - it can carry internal detail (ARNs, hostnames) that
    `/api/health` must not expose.

    `db_user` is the database role the probe query ran as (`current_user`). A
    role name carries no secret, and it is what proves which login the running
    process really uses.

    `stale` is True when this is the last result served while a newer probe is still running.
    """

    ok: bool
    error_class: Optional[str] = None
    component: Optional[str] = None
    db_user: Optional[str] = None
    stale: bool = False


_cache: Optional[AuroraProbeResult] = None
_cache_at: float = 0.0
_inflight: Optional[asyncio.Task] = None


async def _run_probe() -> AuroraProbeResult:
    """Ask Aurora through the Data API whether the workflow tables exist."""
    try:
        row = await get_rds_data_client().execute_one(WORKFLOW_TABLES_SQL)
    except Exception as exc:  # noqa: BLE001 - reports the failure class, never the text.
        return AuroraProbeResult(ok=False, error_class=type(exc).__name__, component="aurora")
    db_user = (row or {}).get("db_user")
    if not row or row.get("snapshots") is not True:
        return AuroraProbeResult(ok=False, component="workflow_snapshots", db_user=db_user)
    if row.get("stops") is not True:
        return AuroraProbeResult(ok=False, component="workflow_session_stops", db_user=db_user)
    return AuroraProbeResult(ok=True, db_user=db_user)


def _remember(task: asyncio.Task) -> None:
    """Keep a finished probe as the latest result, however late it finished."""
    global _cache, _cache_at
    if task.cancelled():
        return
    _cache = task.result()
    _cache_at = time.monotonic()


def _running_probe() -> Optional[asyncio.Task]:
    """The probe still running on this event loop, if any."""
    if _inflight is None or _inflight.done():
        return None
    if _inflight.get_loop() is not asyncio.get_running_loop():
        return None
    return _inflight


def _unanswered() -> AuroraProbeResult:
    """What a caller gets when the probe has not answered in time: the last result, if recent."""
    if _cache is not None and (time.monotonic() - _cache_at) < STALE_LIMIT_SECONDS:
        return replace(_cache, stale=True)
    return AuroraProbeResult(ok=False, error_class="TimeoutError", component="aurora")


async def probe_aurora() -> AuroraProbeResult:
    """Return an Aurora reachability result within `PROBE_TIMEOUT_SECONDS`.

    Returns:
        The latest real probe result: the cached one while it is younger than
        `CACHE_TTL_SECONDS`, else a new probe's. When a probe is still running, or the new one
        does not finish in time, the last result marked `stale`, or a `TimeoutError` failure when
        there is none younger than `STALE_LIMIT_SECONDS`. A value is never invented for the
        occasion.
    """
    global _inflight
    if _cache is not None and (time.monotonic() - _cache_at) < CACHE_TTL_SECONDS:
        return _cache
    task = _running_probe()
    if task is None:
        task = asyncio.ensure_future(_run_probe())
        task.add_done_callback(_remember)
        _inflight = task
    done, _pending = await asyncio.wait({task}, timeout=PROBE_TIMEOUT_SECONDS)
    return task.result() if done and not task.cancelled() else _unanswered()


def reset_probe_cache() -> None:
    """Clear the cached probe result so the next call re-probes. Test-only."""
    global _cache, _cache_at, _inflight
    _cache = None
    _cache_at = 0.0
    _inflight = None
