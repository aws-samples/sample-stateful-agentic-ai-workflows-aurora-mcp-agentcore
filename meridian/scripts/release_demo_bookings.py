"""Release the demo traveler's bookings so rehearsals start from open inventory.

A courtesy hold releases itself when it expires, but a confirmed booking never
does: it counts against the package's capacity for good, which is the point of
confirming it. After a few rehearsals of the "bring it home" beat the Tokyo
package would refuse new holds with ``insufficient_inventory``. This removes
the demo traveler's bookings, their lines and their hold-request identities.
Journeys and checkpoints stay, so System evidence keeps its history.

Usage:
    python scripts/release_demo_bookings.py            # list the demo bookings, delete nothing
    python scripts/release_demo_bookings.py --confirmed-only
    python scripts/release_demo_bookings.py --apply --i-understand-this-changes-aws

The default is a dry run. ``--apply`` deletes as the master login, so it also needs
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
from scripts.identity_release.usage import UsageParser  # noqa: E402

DEMO_TRAVELER = "trv_meridian_demo"
DECOY_TRAVELER = "trv_demo_decoy"
CONFIRM_FLAG = "--i-understand-this-changes-aws"
console = Console()


async def release(
    traveler_id: str, confirmed_only: bool, dry_run: bool, booking_id: str | None = None,
) -> int:
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
        console.print(
            f"  {row['booking_id']}  {row['status']:<10} ${row['total_amount']}  {row['created_at']}"
        )
    if dry_run:
        console.print(f"Dry run: {len(rows)} booking(s) would be released.")
        return len(rows)
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
            for table in ("hold_requests", "booking_lines", "bookings"):
                await client.execute(
                    f"DELETE FROM {table} WHERE booking_id = %s",
                    (selected,), transaction_id=tx,
                )
        client.commit_transaction(tx)
    except BaseException:
        client.rollback_transaction(tx)
        raise
    console.print(f"Released {len(rows)} booking(s) for {traveler_id}.")
    return len(rows)


def main(argv: list[str] | None = None) -> None:
    """Parse the CLI and list, or with both confirm flags delete, the traveler's bookings."""
    parser = UsageParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--traveler", default=DEMO_TRAVELER, choices=[DEMO_TRAVELER, DECOY_TRAVELER],
        help="seeded traveler id to release",
    )
    parser.add_argument(
        "--confirmed-only", action="store_true", help="leave active holds in place"
    )
    parser.add_argument("--apply", action="store_true", help="delete; the default only lists")
    parser.add_argument(
        CONFIRM_FLAG, action="store_true", dest="confirmed",
        help="required with --apply: acknowledges that it deletes bookings",
    )
    parser.add_argument("--booking-id", help="release only this booking owned by the traveler")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="deprecated: listing is already the default; accepted so older runbooks still run",
    )
    args = parser.parse_args(argv)
    if args.dry_run and args.apply:
        parser.error("--dry-run lists only and cannot be combined with --apply.")
    if args.apply and not args.confirmed:
        parser.error(
            f"--apply deletes bookings; add {CONFIRM_FLAG} to run it. Nothing was changed."
        )
    asyncio.run(
        release(args.traveler, args.confirmed_only, not args.apply, args.booking_id)
    )


if __name__ == "__main__":
    main()
