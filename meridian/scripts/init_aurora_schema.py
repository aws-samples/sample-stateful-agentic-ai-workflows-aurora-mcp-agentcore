"""
Initialize Aurora with meridian/backend/db/schema.sql (travel-native DDL).

AWS docs:
  - RDS Data API (used to run DDL):
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html
  - Aurora PostgreSQL extensions (pgvector enabled in schema):
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraPostgreSQL.Extensions.html
"""
import os
import re
from pathlib import Path

import boto3
from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
console = Console()

CLUSTER_ARN = os.getenv("AURORA_CLUSTER_ARN")
SECRET_ARN = os.getenv("AURORA_SECRET_ARN")
DATABASE = os.getenv("AURORA_DATABASE", "meridian")
REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "backend" / "db" / "schema.sql"
RLS_PATH = Path(__file__).resolve().parents[1] / "examples" / "rls_for_agents.sql"
RLS_APP_ROLE_PATH = Path(__file__).resolve().parents[1] / "examples" / "rls_app_role.sql"


def split_sql(script: str) -> list[str]:
    """Split SQL file into executable statements (handles $$ function bodies)."""
    statements: list[str] = []
    current: list[str] = []
    in_dollar = False
    for line in script.splitlines():
        if "$$" in line:
            count = line.count("$$")
            if count % 2 == 1:
                in_dollar = not in_dollar
        current.append(line)
        if not in_dollar and line.rstrip().endswith(";"):
            stmt = "\n".join(current).strip()
            if stmt and not all(
                ln.strip().startswith("--") or not ln.strip() for ln in stmt.splitlines()
            ):
                statements.append(stmt)
            current = []
    tail = "\n".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def execute_sql(client, sql: str, description: str = "") -> None:
    try:
        client.execute_statement(
            resourceArn=CLUSTER_ARN,
            secretArn=SECRET_ARN,
            database=DATABASE,
            sql=sql,
        )
        if description:
            console.print(f"[green]✅ {description}[/green]")
    except Exception as exc:
        console.print(f"[red]❌ {exc}[/red]\n{sql[:200]}...")
        raise


def initialize_database() -> None:
    console.print("\n[bold blue]Initializing Meridian travel schema[/bold blue]")
    if not CLUSTER_ARN or not SECRET_ARN:
        console.print("[red]Missing AURORA_CLUSTER_ARN or AURORA_SECRET_ARN[/red]")
        return

    script = SCHEMA_PATH.read_text()
    statements = split_sql(script)
    client = boto3.client("rds-data", region_name=REGION)
    console.print(f"[cyan]Running {len(statements)} statements from schema.sql[/cyan]\n")

    for i, sql in enumerate(statements, 1):
        first_line = next((ln.strip() for ln in sql.splitlines() if ln.strip() and not ln.strip().startswith("--")), sql[:60])
        execute_sql(client, sql, f"[{i}/{len(statements)}] {first_line[:70]}")

    if RLS_PATH.exists():
        console.print("\n[cyan]Applying RLS policies + audit log[/cyan]")
        rls_statements = split_sql(RLS_PATH.read_text())
        for i, sql in enumerate(rls_statements, 1):
            first_line = next(
                (ln.strip() for ln in sql.splitlines() if ln.strip() and not ln.strip().startswith("--")),
                sql[:60],
            )
            execute_sql(client, sql, f"[rls {i}/{len(rls_statements)}] {first_line[:70]}")

    if RLS_APP_ROLE_PATH.exists():
        console.print("\n[cyan]Applying least-privilege RLS app role[/cyan]")
        role_statements = split_sql(RLS_APP_ROLE_PATH.read_text())
        for i, sql in enumerate(role_statements, 1):
            first_line = next(
                (ln.strip() for ln in sql.splitlines() if ln.strip() and not ln.strip().startswith("--")),
                sql[:60],
            )
            execute_sql(client, sql, f"[rls-role {i}/{len(role_statements)}] {first_line[:70]}")

    console.print("\n[bold green]Schema ready[/bold green]")


if __name__ == "__main__":
    initialize_database()
