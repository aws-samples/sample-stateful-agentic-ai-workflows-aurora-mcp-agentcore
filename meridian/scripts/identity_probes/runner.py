"""Run the probe plan and account for everything it created."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from scripts.identity_probes.probes import THREAD_PREFIX, Context, Ports, run_probe, select
from scripts.identity_probes.receipt import FULL, Receipt, scrub


class Cleanup(Protocol):
    """What the run removes when it is done."""

    def purge_thread(self, thread: str) -> None:
        """Remove a journey or thread the run created, and its snapshots and executions."""

    def release_bookings(self, booking_ids: list[str]) -> int:
        """Remove bookings (with their lines and hold requests); return how many."""

    def leftovers(self, prefix: str, booking_ids: list[str]) -> int:
        """Threads with the run's prefix plus listed bookings that still exist."""


@dataclass(frozen=True)
class Header:
    """The facts a receipt records about the deployment and the checkout."""

    at: str
    git_sha: str
    region: str
    pool_suffix: str
    design: str
    site_host: str
    mode: str = FULL


def _problem(what: str, exc: Exception) -> str:
    return scrub(f"{what}: {type(exc).__name__}: {exc}")


def tidy(cleanup: Cleanup, ctx: Context) -> dict[str, Any]:
    """Purge the run's threads, release its bookings and count what remains.

    A step that cannot run is reported as a problem. A check that cannot run counts as one
    leftover, because an unknown state is not a clean one.
    """
    problems: list[str] = []
    purged = 0
    for thread in dict.fromkeys(ctx.threads):
        try:
            cleanup.purge_thread(thread)
            purged += 1
        except Exception as exc:  # noqa: BLE001 - reported in the receipt
            problems.append(_problem(thread, exc))
    ids = sorted(ctx.created_bookings)
    released = 0
    try:
        released = cleanup.release_bookings(ids) if ids else 0
    except Exception as exc:  # noqa: BLE001 - reported in the receipt
        problems.append(_problem("release", exc))
    try:
        leftovers = cleanup.leftovers(THREAD_PREFIX + ctx.run_id, ids)
    except Exception as exc:  # noqa: BLE001 - reported in the receipt
        problems.append(_problem("leftover check", exc))
        leftovers = 1
    return {"threads_purged": purged, "bookings_released": released, "leftovers": leftovers,
            "problems": problems}


def run_proof(
    ports: Ports, cleanup: Cleanup, ctx: Context, header: Header, *, jordan_only: bool = False
) -> Receipt:
    """Run the probes in order, then always clean up, and return the receipt.

    An interrupt stops the plan, runs the cleanup and then propagates, so no receipt is written
    for an interrupted run.
    """
    receipt = Receipt(
        at=header.at, git_sha=header.git_sha, region=header.region,
        pool_suffix=header.pool_suffix, design=header.design, site_host=header.site_host,
        mode=header.mode,
    )
    try:
        for spec in select(jordan_only):
            receipt.outcomes.append(run_probe(spec, ports, ctx))
    finally:
        receipt.cleanup = tidy(cleanup, ctx)
    return receipt
