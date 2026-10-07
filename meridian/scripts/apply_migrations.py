"""Apply tracked, idempotent Meridian migrations through the RDS Data API.

Unlike init_aurora_schema.py, this command never recreates the base schema or
removes data. Use it for an existing Aurora cluster before deploying code that
depends on a newer database contract.
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


def _pending_paths(applied: set[str]) -> list[Path]:
    return [
        path
        for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))
        if path.name not in applied
    ]


def _apply_migration(client, path: Path) -> None:
    transaction_id = client.begin_transaction(
        resourceArn=CLUSTER_ARN,
        secretArn=SECRET_ARN,
        database=DATABASE,
    )["transactionId"]
    try:
        for statement in split_sql(path.read_text()):
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--pending", action="store_true",
        help="print the pending migration names and exit; writes nothing",
    )
    args = parser.parse_args(argv)
    if not CLUSTER_ARN or not SECRET_ARN:
        console.print("[red]Missing AURORA_CLUSTER_ARN or AURORA_SECRET_ARN[/red]")
        return 2
    if not MIGRATIONS_DIR.exists():
        console.print("[yellow]No migrations directory found.[/yellow]")
        return 0

    client = boto3.client("rds-data", region_name=REGION)
    if args.pending:
        for path in _pending_paths(_applied_migrations_read_only(client)):
            print(path.name)
        return 0
    _ensure_migration_table(client)
    pending = _pending_paths(_applied_migrations(client))

    if not pending:
        console.print("[green]No pending migrations.[/green]")
        return 0

    for path in pending:
        console.print(f"[cyan]Applying {path.name}[/cyan]")
        _apply_migration(client, path)
    console.print(f"[bold green]Applied {len(pending)} migration(s).[/bold green]")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ClientError as exc:
        console.print(f"[red]RDS Data API migration failed: {exc}[/red]")
        raise SystemExit(1) from exc
