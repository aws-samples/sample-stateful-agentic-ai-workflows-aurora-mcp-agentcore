"""Give back the demo traveler's active holds so rehearsals start from open inventory.

A courtesy hold releases itself when it expires, but a confirmed booking never does: it counts
against the package's capacity for good, which is the point of confirming it. By default this
lists the traveler's bookings. With ``--apply`` it gives each active hold back through the
governed Gateway release tool, as the traveler, and keeps every row: the booking, its hold
request and its action audit records stay as evidence. Confirmed bookings are not holds, so they
are listed and kept.

Usage:
    python scripts/release_demo_bookings.py            # list the demo bookings, change nothing
    python scripts/release_demo_bookings.py --dry-run  # the same, said explicitly
    python scripts/release_demo_bookings.py --apply --i-understand-this-changes-aws

To free the capacity a confirmed booking uses, a database you are willing to discard can delete
bookings instead:
    python scripts/release_demo_bookings.py --apply --i-understand-this-changes-aws \
        --delete-from-disposable-database [--confirmed-only] [--booking-id <id>]
That is an administrative deletion, not a release. It removes the bookings, their lines, hold
request identities and booking_action_audit rows, so any audit export already taken no longer
matches Aurora (row-deleted). Never use it on a database whose audit trail matters.
Journeys and snapshots stay either way.

The default is a dry run. ``--apply`` changes data, so it also needs
``--i-understand-this-changes-aws``; without it the script exits 3 and changes nothing.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from backend.db.rds_data_client import get_rds_data_client  # noqa: E402
from scripts.governed_release import CallLedger, release_bookings  # noqa: E402
from scripts.identity_release.usage import UsageParser  # noqa: E402

DEMO_TRAVELER = "trv_meridian_demo"
DECOY_TRAVELER = "trv_demo_decoy"
CONFIRM_FLAG = "--i-understand-this-changes-aws"
DELETE_FLAG = "--delete-from-disposable-database"
console = Console()


async def release(
    traveler_id: str, confirmed_only: bool, dry_run: bool, booking_id: str | None = None,
    delete: bool = False,
) -> int:
    """List the traveler's bookings, then give back the held ones, or delete when ``delete``.

    Returns the number of bookings listed. Without ``delete`` no row is deleted: held bookings
    go back through the Gateway and the rest are kept.
    """
    client = get_rds_data_client()
    status_filter = "AND status = 'confirmed'" if confirmed_only else ""
    rows = await client.execute(
        "SELECT booking_id, status, total_amount::TEXT AS total_amount, "
        "created_at::TIMESTAMPTZ::TEXT AS created_at FROM bookings "
        f"WHERE traveler_id = %s {status_filter} "
        "AND (%s::text IS NULL OR booking_id = %s) ORDER BY created_at",
        (traveler_id, booking_id, booking_id),
    )
    if not rows:
        console.print(f"No bookings on record for {traveler_id}.")
        return 0
    for row in rows:
        console.print(f"  {row['booking_id']}  {row['status']:<10} ${row['total_amount']}  "
                      f"{row['created_at']}")
    if dry_run:
        action = ("deleted (audit rows too)" if delete
                  else "kept; each hold among them that is still active would be released")
        console.print(f"Dry run: {len(rows)} booking(s) listed, nothing changed. With --apply "
                      f"they would be {action}.")
        return len(rows)
    if delete:
        await _delete(client, traveler_id, rows, status_filter)
    else:
        await _release_held(client, traveler_id, rows)
    return len(rows)


async def _release_held(client, traveler_id: str, rows: list[dict]) -> None:
    held = [row["booking_id"] for row in rows if row["status"] == "held"]
    kept = [f"{row['booking_id']} ({row['status']})" for row in rows if row["status"] != "held"]
    ledger = CallLedger()
    problems = await release_bookings(client, traveler_id, held, ledger)
    for problem in problems:
        console.print(f"FAILED: {problem}", style="red")
    if kept:
        console.print(f"Kept, not holds: {', '.join(kept)}.")
    unresolved = {booking for booking in held if any(f"booking {booking} " in p for p in problems)}
    released = [b for b in held if ledger.made(b) and b not in unresolved]
    lapsed = len(held) - len(released) - len(unresolved)
    console.print(f"Released {len(released)} active hold(s) for {traveler_id}; {lapsed} held "
                  "booking(s) had already lapsed and needed no release; no row was deleted.")
    if problems:
        raise SystemExit(1)


async def _delete(client, traveler_id: str, rows: list[dict], status_filter: str) -> None:
    tx = client.begin_transaction()
    try:
        for row in rows:
            selected = row["booking_id"]
            # Recheck ownership and status under a row lock before any deletion.
            current = await client.execute_one(
                "SELECT booking_id FROM bookings WHERE booking_id = %s "
                f"AND traveler_id = %s {status_filter} FOR UPDATE",
                (selected, traveler_id), transaction_id=tx,
            )
            if not current:
                raise RuntimeError("A selected booking changed; retry the dry run before release.")
            for table in ("booking_action_audit", "hold_requests", "booking_lines", "bookings"):
                await client.execute(
                    f"DELETE FROM {table} WHERE booking_id = %s",
                    (selected,), transaction_id=tx,
                )
        client.commit_transaction(tx)
    except BaseException:
        client.rollback_transaction(tx)
        raise
    console.print(
        f"Deleted {len(rows)} booking(s) and their action audit records for {traveler_id}.")


def main(argv: list[str] | None = None) -> None:
    """Parse the CLI: list by default; with both confirm flags release the traveler's holds."""
    parser = UsageParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--traveler", default=DEMO_TRAVELER, choices=[DEMO_TRAVELER, DECOY_TRAVELER],
        help="seeded traveler id to release",
    )
    parser.add_argument("--confirmed-only", action="store_true",
                        help="only confirmed bookings (listing, or with the delete flag)")
    parser.add_argument("--apply", action="store_true", help="act; the default only lists")
    parser.add_argument("--dry-run", action="store_true",
                        help="list only: the default, said explicitly")
    parser.add_argument(
        CONFIRM_FLAG, action="store_true", dest="confirmed",
        help="required with --apply: acknowledges that it changes bookings",
    )
    parser.add_argument(
        DELETE_FLAG, action="store_true", dest="delete",
        help="with --apply: delete bookings and their audit rows instead of releasing holds; "
             "only for a database you discard",
    )
    parser.add_argument("--booking-id", help="release only this booking owned by the traveler")
    args = parser.parse_args(argv)
    if args.apply and args.dry_run:
        parser.error("--dry-run lists only; drop --apply or drop --dry-run.")
    if args.apply and not args.confirmed:
        parser.error(f"--apply changes bookings; add {CONFIRM_FLAG} to run it. "
                     "Nothing was changed.")
    if args.delete and not args.apply:
        parser.error(f"{DELETE_FLAG} needs --apply. Nothing was changed.")
    if args.apply and args.confirmed_only and not args.delete:
        parser.error(f"confirmed bookings are not holds; --confirmed-only with --apply needs "
                     f"{DELETE_FLAG}. Nothing was changed.")
    asyncio.run(release(args.traveler, args.confirmed_only, not args.apply, args.booking_id,
                        args.delete))


if __name__ == "__main__":
    main()
