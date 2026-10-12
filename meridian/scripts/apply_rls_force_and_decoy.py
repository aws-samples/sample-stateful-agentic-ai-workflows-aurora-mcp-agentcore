#!/usr/bin/env python3
"""One-shot: enable FORCE ROW LEVEL SECURITY on the Phase 4 tables and seed a
decoy traveler, so the RLS probe (/api/diagnostics/rls-probe) shows real
per-traveler isolation (unscoped > scoped) WITHOUT a full schema re-init or
re-seed.

Why this exists: the schema only ran ``ENABLE ROW LEVEL SECURITY``. PostgreSQL
exempts the table owner from RLS unless ``FORCE`` is set, and the RDS Data API
connects as the owner — so the policies were silently bypassed for the app's
own connection (scoped == unscoped, and an unknown traveler still saw all
rows). This applies the FORCE flags and adds a second traveler's preferences.

Idempotent: FORCE is a no-op if already set; the decoy uses ON CONFLICT.

Usage:
    cd meridian && python scripts/apply_rls_force_and_decoy.py
"""

import os
import sys
import uuid
from pathlib import Path

import boto3
from dotenv import load_dotenv
from rich.console import Console

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.seed_data import DECOY_PREFERENCES, DECOY_TRAVELER_ID  # noqa: E402

load_dotenv()
console = Console()

CLUSTER_ARN = os.getenv("AURORA_CLUSTER_ARN")
SECRET_ARN = os.getenv("AURORA_SECRET_ARN")
DATABASE = os.getenv("AURORA_DATABASE", "meridian")
REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

FORCE_TABLES = [
    "traveler_preferences",
    "conversation_messages",
    "trip_interactions",
    "conversations",
    "bookings",
]


def main() -> int:
    if not CLUSTER_ARN or not SECRET_ARN:
        console.print("[red]Missing Aurora credentials in .env[/red]")
        return 1
    rds = boto3.client("rds-data", region_name=REGION)

    def run(sql: str, params=None):
        kwargs = dict(resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN, database=DATABASE, sql=sql)
        if params:
            kwargs["parameters"] = params
        return rds.execute_statement(**kwargs)

    console.print("[cyan]Enabling + forcing RLS on Phase 4 tables…[/cyan]")
    for tbl in FORCE_TABLES:
        # ENABLE is the master switch — FORCE without ENABLE does NOT filter.
        # Set both so this is correct regardless of prior state.
        run(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY")
        run(f"ALTER TABLE {tbl} FORCE ROW LEVEL SECURITY")
        console.print(f"  [green]✓[/green] ENABLE + FORCE on {tbl}")

    console.print("[cyan]Seeding decoy traveler…[/cyan]")
    run(
        """
        INSERT INTO travelers (traveler_id, full_name, email, home_airport)
        VALUES (:t, :n, :e, :a)
        ON CONFLICT (traveler_id) DO UPDATE SET full_name = EXCLUDED.full_name
        """,
        [
            {"name": "t", "value": {"stringValue": DECOY_TRAVELER_ID}},
            {"name": "n", "value": {"stringValue": "Jordan Lee"}},
            {"name": "e", "value": {"stringValue": "jordan.lee@example.com"}},
            {"name": "a", "value": {"stringValue": "SFO"}},
        ],
    )
    for pref in DECOY_PREFERENCES:
        run(
            """
            INSERT INTO traveler_preferences (
                preference_id, traveler_id, preference_type, preference_key,
                preference_value, confidence, signal_count, source
            ) VALUES (:id, :t, :pt, :pk, :pv, 0.9, 1, 'decoy_seed')
            ON CONFLICT (traveler_id, preference_type, preference_key)
            DO UPDATE SET preference_value = EXCLUDED.preference_value
            """,
            [
                {"name": "id", "value": {"stringValue": f"pref_{uuid.uuid4().hex[:10]}"}},
                {"name": "t", "value": {"stringValue": DECOY_TRAVELER_ID}},
                {"name": "pt", "value": {"stringValue": pref["preference_type"]}},
                {"name": "pk", "value": {"stringValue": pref["preference_key"]}},
                {"name": "pv", "value": {"stringValue": pref["preference_value"]}},
            ],
        )
    console.print(f"  [green]✓[/green] {len(DECOY_PREFERENCES)} decoy preferences")
    console.print("\n[bold green]Done.[/bold green] Re-run the RLS probe to see unscoped > scoped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
