"""Run the probe plan and account for everything it created."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from scripts.identity_probes.probes import (
    THREAD_PREFIX,
    BookingRef,
    Context,
    Ports,
    run_probe,
    select,
    snapshot_bookings,
)
from scripts.identity_probes.receipt import FULL, Receipt, scrub


class Cleanup(Protocol):
    """What the run removes when it is done."""

    def purge_thread(self, thread: str) -> None:
        """Remove a journey or thread the run created, and its snapshots and executions."""

    def release_bookings(self, bookings: list[BookingRef]) -> int:
        """Remove ``(traveler, booking)`` pairs (with lines and hold requests); return how many."""

    def leftovers(self, prefix: str, baseline: Mapping[str, frozenset[str]]) -> list[str]:
        """Describe what remains: threads with the prefix and bookings not in ``baseline``."""


@dataclass(frozen=True)
class Header:
    """The facts a receipt records about the deployment and the checkout."""

    at: str
    git_sha: str
    region: str
    design: str
    site_host: str
    mode: str = FULL


def _problem(what: str, exc: BaseException) -> str:
    kind = "interrupted" if isinstance(exc, KeyboardInterrupt) else type(exc).__name__
    return scrub(f"{what}: {kind}: {exc}" if str(exc) else f"{what}: {kind}")


def _attempt(problems: list[str], what: str, step: Callable[[], Any], default: Any) -> Any:
    """``step()``'s value, or ``default`` after noting a failure or a Ctrl-C as a problem.

    A second interrupt during cleanup must not abandon the steps after it, so it is recorded
    here and the caller carries on.
    """
    try:
        return step()
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - reported in the receipt
        problems.append(_problem(what, exc))
        return default


def tidy(cleanup: Cleanup, ctx: Context) -> dict[str, Any]:
    """Release the run's bookings, purge its threads and list what remains.

    Bookings go first, by exact id, because purging a thread removes the hold requests that
    tie a booking to the run. Every step runs even when an earlier one failed or was
    interrupted. A step that cannot run is reported as a problem. A check that cannot run
    counts as one leftover, because an unknown state is not a clean one.
    """
    problems: list[str] = []
    owned = sorted(ctx.owned_bookings)
    released = _attempt(
        problems, "release", lambda: cleanup.release_bookings(owned) if owned else 0, 0)
    purged = 0
    for thread in dict.fromkeys(ctx.threads):
        purged += _attempt(problems, thread, lambda t=thread: _purge(cleanup, t), 0)
    items = _leftover_items(cleanup, ctx, problems)
    return {"threads_purged": purged, "bookings_released": released, "leftovers": len(items),
            "leftover_items": [scrub(item) for item in items], "problems": problems}


def _leftover_items(cleanup: Cleanup, ctx: Context, problems: list[str]) -> list[str]:
    prefix = THREAD_PREFIX + ctx.run_id
    items = _attempt(problems, "leftover check",
                     lambda: cleanup.leftovers(prefix, ctx.baseline or {}), None)
    listed = ["the leftover check could not run"] if items is None else list(items)
    if ctx.baseline is None:
        problems.append("leftover check: no booking baseline was taken before the plan")
        listed.append("the leftover check had no booking baseline")
    return listed


def _purge(cleanup: Cleanup, thread: str) -> int:
    cleanup.purge_thread(thread)
    return 1


def run_proof(
    ports: Ports, cleanup: Cleanup, ctx: Context, header: Header, *, jordan_only: bool = False
) -> Receipt:
    """Run the probes in order, then always clean up, and return the receipt.

    An interrupt or crash stops the plan, runs the cleanup and then propagates with a note that
    says what the cleanup found, so no receipt is returned for a run that did not finish.
    """
    receipt = Receipt(
        at=header.at, git_sha=header.git_sha, region=header.region,
        design=header.design, site_host=header.site_host,
        mode=header.mode,
    )
    try:
        ports.database.preflight()
        ctx.baseline = snapshot_bookings(ports)
        for spec in select(jordan_only):
            receipt.outcomes.append(run_probe(spec, ports, ctx))
    except BaseException as exc:
        exc.add_note(describe_cleanup(tidy(cleanup, ctx)))
        raise
    receipt.cleanup = tidy(cleanup, ctx)
    receipt.notes = ctx.residue_notes()
    return receipt


def describe_cleanup(notes: dict[str, Any]) -> str:
    """One line saying what the cleanup removed and what it could not account for."""
    problems = "; ".join(notes["problems"]) or "none"
    return (f"cleanup: {notes['threads_purged']} thread(s) purged, {notes['bookings_released']} "
            f"booking(s) released, {notes['leftovers']} leftover(s), problems: {problems}")
