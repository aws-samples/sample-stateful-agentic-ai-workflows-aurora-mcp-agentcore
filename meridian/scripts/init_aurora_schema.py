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


def _statement_head(sql: str) -> str:
    """First meaningful line of a statement, for progress output."""
    return next(
        (ln.strip() for ln in sql.splitlines() if ln.strip() and not ln.strip().startswith("--")),
        sql[:60],
    )


def _grants_on_objects(sql: str) -> bool:
    """True for a GRANT that names database objects rather than a role.

    ``GRANT ... ON <tables>`` needs those tables to exist; ``GRANT <role> TO
    <role>`` only needs the roles. The ON is what separates them, and it is
    what decides whether a statement runs before or after the table DDL.
    """
    head = _statement_head(sql).upper()
    return head.startswith("GRANT") and re.search(r"\bON\b", sql.upper()) is not None


def _apply(client, statements: list, tag: str, heading: str) -> None:
    """Run a phase of statements, reporting progress."""
    if not statements:
        return
    console.print(f"\n[cyan]{heading}[/cyan]")
    for i, sql in enumerate(statements, 1):
        execute_sql(client, sql, f"[{tag} {i}/{len(statements)}] {_statement_head(sql)[:70]}")


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

    # The two RLS files depend on each other, so neither can be applied whole
    # before the other: rls_app_role.sql grants on agent_audit_log, which
    # rls_for_agents.sql creates, while rls_for_agents.sql grants EXECUTE to
    # meridian_app, which rls_app_role.sql creates. Applying the role file
    # first made a fresh database fail on the grant; an already-seeded database
    # hid it, because the table was left over from a previous run.
    #
    # Split it by dependency instead of by file: the role has to exist before
    # anything grants to it, and the objects have to exist before anything
    # grants on them.
    role_setup, object_grants = [], []
    if RLS_APP_ROLE_PATH.exists():
        for sql in split_sql(RLS_APP_ROLE_PATH.read_text()):
            (object_grants if _grants_on_objects(sql) else role_setup).append(sql)

    _apply(client, role_setup, "rls-role", "Creating the least-privilege app role")
    if RLS_PATH.exists():
        _apply(
            client,
            split_sql(RLS_PATH.read_text()),
            "rls",
            "Applying RLS policies, functions, and audit log",
        )
    _apply(client, object_grants, "rls-grant", "Granting table access to the app role")

    console.print("\n[bold green]Schema ready[/bold green]")


if __name__ == "__main__":
    initialize_database()
