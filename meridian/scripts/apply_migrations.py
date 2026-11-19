"""Apply tracked, idempotent Meridian migrations through the RDS Data API.

Unlike init_aurora_schema.py, this command never recreates the base schema or
removes data. Use it for an existing Aurora cluster before deploying code that
depends on a newer database contract.

Run without a mode flag it only lists the pending migrations and prints this usage; it writes
nothing. Apply one migration at a time, as the guides describe:

    python scripts/apply_migrations.py --pending
    python scripts/apply_migrations.py --dry-run --only <first-pending-migration.sql>
    python scripts/apply_migrations.py --only <first-pending-migration.sql>

Migrations 026 to 029 span an application cutover on an existing installation, so each one is
applied on its own with the running application upgraded between them
(docs/BOOKING_FUNCTIONS_UPGRADE.md).
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import sys

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from rich.console import Console

# Runnable as `python scripts/apply_migrations.py` from the project root, which
# puts scripts/ on sys.path rather than the project root itself.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.init_aurora_schema import split_sql  # noqa: E402

load_dotenv()

console = Console()
CLUSTER_ARN = os.getenv("AURORA_CLUSTER_ARN")
SECRET_ARN = os.getenv("AURORA_SECRET_ARN")
DATABASE = os.getenv("AURORA_DATABASE", "meridian")
REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
# Applied only when the operator adopts automatic password rotation (docs/OPERATIONS.md).
OPT_IN_MIGRATIONS = frozenset({"034_login_rotation_clones.sql"})


def _execute(client, sql: str, transaction_id: str | None = None, **kwargs):
    request = {
        "resourceArn": CLUSTER_ARN,
        "secretArn": SECRET_ARN,
        "database": DATABASE,
        "sql": sql,
        **kwargs,
    }
    if transaction_id:
        request["transactionId"] = transaction_id
    return client.execute_statement(**request)


def _ensure_migration_table(client) -> None:
    _execute(
        client,
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            migration_name VARCHAR(255) PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
    )


def _applied_migrations(client) -> set[str]:
    response = _execute(
        client,
        "SELECT migration_name FROM schema_migrations ORDER BY migration_name",
    )
    return {
        row[0]["stringValue"]
        for row in response.get("records", [])
        if row and "stringValue" in row[0]
    }


def _applied_migrations_read_only(client) -> set[str]:
    """The applied names without creating the tracking table when it is absent."""
    exists = _execute(client, "SELECT to_regclass('public.schema_migrations')::text")
    cells = (exists.get("records") or [[{"isNull": True}]])[0]
    if not cells or cells[0].get("isNull") or "stringValue" not in cells[0]:
        return set()
    return _applied_migrations(client)


def _pending_paths(applied: set[str], with_rotation: bool = False) -> list[Path]:
    """Unapplied migrations in order; opt-in ones appear only when asked for."""
    return [
        path
        for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))
        if path.name not in applied
        and (with_rotation or path.name not in OPT_IN_MIGRATIONS)
    ]


def _apply_migration(client, path: Path) -> None:
    statements = split_sql(path.read_text())
    transaction_id = client.begin_transaction(
        resourceArn=CLUSTER_ARN,
        secretArn=SECRET_ARN,
        database=DATABASE,
    )["transactionId"]
    try:
        for statement in statements:
            _execute(client, statement, transaction_id)
        _execute(
            client,
            "INSERT INTO schema_migrations (migration_name) VALUES (:migration_name)",
            transaction_id,
            parameters=[
                {
                    "name": "migration_name",
                    "value": {"stringValue": path.name},
                }
            ],
        )
        client.commit_transaction(
            resourceArn=CLUSTER_ARN,
            secretArn=SECRET_ARN,
            transactionId=transaction_id,
        )
    except Exception:
        client.rollback_transaction(
            resourceArn=CLUSTER_ARN,
            secretArn=SECRET_ARN,
            transactionId=transaction_id,
        )
        raise


def _dry_run_migration(client, path: Path) -> int:
    """Run one migration's statements in a transaction, then roll it back.

    Returns:
        The number of statements that ran. Nothing is committed and the migration is not
        recorded in ``schema_migrations``.
    """
    statements = split_sql(path.read_text())
    transaction_id = client.begin_transaction(
        resourceArn=CLUSTER_ARN,
        secretArn=SECRET_ARN,
        database=DATABASE,
    )["transactionId"]
    try:
        for statement in statements:
            _execute(client, statement, transaction_id)
    finally:
        client.rollback_transaction(
            resourceArn=CLUSTER_ARN,
            secretArn=SECRET_ARN,
            transactionId=transaction_id,
        )
    return len(statements)


def _select_pending(
    client, only: str | None = None, with_rotation: bool = False
) -> list[Path]:
    pending = _pending_paths(_applied_migrations_read_only(client), with_rotation)
    if only and (not pending or pending[0].name != only):
        first = pending[0].name if pending else "none"
        raise ValueError(f"--only must name the first pending migration ({first})")
    return pending[:1] if only else pending


def _dry_run_pending(
    client, only: str | None = None, with_rotation: bool = False
) -> int:
    pending = _select_pending(client, only, with_rotation)
    if not pending:
        print("No pending migrations.")
        return 0
    for path in pending:
        count = _dry_run_migration(client, path)
        print(f"Dry run: {path.name} ran {count} statement(s) and was rolled back.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0], allow_abbrev=False,
        usage="apply_migrations.py (--pending | --dry-run --only NAME.sql | --only NAME.sql) "
              "[--with-rotation]")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--pending", action="store_true",
        help="print the pending migration names and exit; writes nothing",
    )
    mode.add_argument(
        "--dry-run", action="store_true",
        help="run each pending migration in a transaction and roll it back; changes nothing",
    )
    parser.add_argument("--only", metavar="MIGRATION.sql",
                        help="run only the first pending migration, named explicitly")
    parser.add_argument("--with-rotation", action="store_true",
                        help="also include 034_login_rotation_clones.sql; only when adopting "
                             "automatic password rotation")
    args = parser.parse_args(argv)
    bare = not (args.pending or args.dry_run or args.only)
    if not CLUSTER_ARN or not SECRET_ARN:
        console.print("[red]Missing AURORA_CLUSTER_ARN or AURORA_SECRET_ARN[/red]")
        if bare:
            parser.print_usage(sys.stderr)
        return 2
    if not MIGRATIONS_DIR.exists():
        console.print("[yellow]No migrations directory found.[/yellow]")
        return 0

    client = boto3.client("rds-data", region_name=REGION)
    try:
        pending = _select_pending(client, args.only, args.with_rotation)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        return 2
    if args.pending or bare:
        return _list_pending(pending, parser, bare=bare)
    if args.dry_run:
        return _dry_run_pending(client, args.only, args.with_rotation)
    _ensure_migration_table(client)

    for path in pending:
        console.print(f"[cyan]Applying {path.name}[/cyan]")
        _apply_migration(client, path)
    console.print(f"[bold green]Applied {len(pending)} migration(s).[/bold green]")
    return 0


def _list_pending(pending: list[Path], parser: argparse.ArgumentParser, *, bare: bool) -> int:
    """Print the pending names; with ``bare`` also the usage, and exit 2 so nothing sees success."""
    print("\n".join(path.name for path in pending) or "No pending migrations.")
    if not bare:
        return 0
    print("\nNothing was applied. Name the migration to dry-run or apply:", file=sys.stderr)
    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ClientError as exc:
        console.print(f"[red]RDS Data API migration failed: {exc}[/red]")
        raise SystemExit(1) from exc
