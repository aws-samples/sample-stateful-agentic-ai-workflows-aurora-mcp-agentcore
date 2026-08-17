"""Apply tracked, idempotent Meridian migrations through the RDS Data API.

Unlike init_aurora_schema.py, this command never recreates the base schema or
removes data. Use it for an existing Aurora cluster before deploying code that
depends on a newer database contract.
"""

from __future__ import annotations

import os
from pathlib import Path

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from rich.console import Console

from scripts.init_aurora_schema import split_sql

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


def main() -> int:
    if not CLUSTER_ARN or not SECRET_ARN:
        console.print("[red]Missing AURORA_CLUSTER_ARN or AURORA_SECRET_ARN[/red]")
        return 2
    if not MIGRATIONS_DIR.exists():
        console.print("[yellow]No migrations directory found.[/yellow]")
        return 0

    client = boto3.client("rds-data", region_name=REGION)
    _ensure_migration_table(client)
    applied = _applied_migrations(client)
    pending = [
        path
        for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))
        if path.name not in applied
    ]

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
