"""Release the demo traveler's bookings so rehearsals start from open inventory.

A courtesy hold releases itself when it expires, but a confirmed booking never
does: it counts against the package's capacity for good, which is the point of
confirming it. After a few rehearsals of the "bring it home" beat the Tokyo
package would refuse new holds with ``insufficient_inventory``. This removes
the demo traveler's bookings, their lines and their hold-request identities.
Journeys and checkpoints stay, so System evidence keeps its history.

Usage:
    python scripts/release_demo_bookings.py            # remove every demo booking
    python scripts/release_demo_bookings.py --confirmed-only
    python scripts/release_demo_bookings.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from backend.db.rds_data_client import get_rds_data_client  # noqa: E402

DEMO_TRAVELER = "trv_meridian_demo"
console = Console()


async def release(traveler_id: str, confirmed_only: bool, dry_run: bool) -> int:
    client = get_rds_data_client()
    status_filter = "AND status = 'confirmed'" if confirmed_only else ""
    rows = await client.execute(
        "SELECT booking_id, status, total_amount::TEXT AS total_amount, "
        "created_at::TIMESTAMPTZ::TEXT AS created_at FROM bookings "
        f"WHERE traveler_id = %s {status_filter} ORDER BY created_at",
        (traveler_id,),
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
    for row in rows:
        booking_id = row["booking_id"]
        await client.execute("DELETE FROM hold_requests WHERE booking_id = %s", (booking_id,))
        await client.execute("DELETE FROM booking_lines WHERE booking_id = %s", (booking_id,))
        await client.execute("DELETE FROM bookings WHERE booking_id = %s", (booking_id,))
    console.print(f"Released {len(rows)} booking(s) for {traveler_id}.")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--traveler", default=DEMO_TRAVELER, help="traveler id to release")
    parser.add_argument(
        "--confirmed-only", action="store_true", help="leave active holds in place"
    )
    parser.add_argument("--dry-run", action="store_true", help="list without deleting")
    args = parser.parse_args()
    asyncio.run(release(args.traveler, args.confirmed_only, args.dry_run))


if __name__ == "__main__":
    main()
