"""Apply the identity-binding migration and authorize this AWS caller for Jordan."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import boto3
from dotenv import load_dotenv
from rich.console import Console

try:
    from .init_aurora_schema import split_sql
except ImportError:
    from init_aurora_schema import split_sql

load_dotenv()

console = Console()
REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
CLUSTER_ARN = os.getenv("AURORA_CLUSTER_ARN")
SECRET_ARN = os.getenv("AURORA_SECRET_ARN")
DATABASE = os.getenv("AURORA_DATABASE", "meridian")
TRAVELER_ID = os.getenv("MERIDIAN_DEMO_TRAVELER_ID", "trv_meridian_demo")
DECOY_TRAVELER_ID = "trv_demo_decoy"
MIGRATION = Path(__file__).resolve().parent / "migrations" / "005_bind_identity_to_traveler.sql"


def refuse_decoy() -> None:
    """The decoy gets no workload binding, so the workload grant refuses it as RLS does."""
    if TRAVELER_ID == DECOY_TRAVELER_ID:
        raise SystemExit(
            f"Refusing to bind a workload to {DECOY_TRAVELER_ID}: the decoy has no aws_iam "
            "binding by design (docs/OPERATIONS.md, Sign-in and who is calling). Unset "
            "MERIDIAN_DEMO_TRAVELER_ID or set it to trv_meridian_demo."
        )


def execute(client, sql: str, parameters: list[dict] | None = None) -> None:
    kwargs = {
        "resourceArn": CLUSTER_ARN,
        "secretArn": SECRET_ARN,
        "database": DATABASE,
        "sql": sql,
    }
    if parameters:
        kwargs["parameters"] = parameters
    client.execute_statement(**kwargs)


def bind(
    db,
    *,
    provider: str,
    subject_id: str,
    principal: str,
) -> None:
    refuse_decoy()
    digest = hashlib.sha256(
        f"{provider}:{subject_id}:{TRAVELER_ID}".encode()
    ).hexdigest()[:16]
    execute(
        db,
        """
        INSERT INTO traveler_identity_bindings (
            binding_id, identity_provider, subject_id, traveler_id,
            status, granted_by
        ) VALUES (
            :binding_id, :provider, :subject_id, :traveler_id,
            'active', :principal
        )
        ON CONFLICT (identity_provider, subject_id, traveler_id) DO UPDATE SET
            status = 'active',
            granted_by = EXCLUDED.granted_by,
            expires_at = NULL
        """,
        [
            {"name": "binding_id", "value": {"stringValue": f"bind_{digest}"}},
            {"name": "provider", "value": {"stringValue": provider}},
            {"name": "subject_id", "value": {"stringValue": subject_id}},
            {"name": "traveler_id", "value": {"stringValue": TRAVELER_ID}},
            {"name": "principal", "value": {"stringValue": principal}},
        ],
    )
    console.print(
        f"[green]Authorized {provider}:{subject_id} for {TRAVELER_ID}[/green]"
    )


def main() -> None:
    refuse_decoy()
    if not CLUSTER_ARN or not SECRET_ARN:
        raise SystemExit("AURORA_CLUSTER_ARN and AURORA_SECRET_ARN are required")

    db = boto3.client("rds-data", region_name=REGION)
    for statement in split_sql(MIGRATION.read_text()):
        execute(db, statement)

    caller = boto3.client("sts").get_caller_identity()
    principal = caller.get("Arn", "unknown")
    subject_id = caller.get("UserId", "").split(":", 1)[0]
    if not subject_id:
        raise SystemExit("STS GetCallerIdentity did not return a stable UserId")

    bind(
        db,
        provider="aws_iam",
        subject_id=subject_id,
        principal=principal,
    )

    try:
        from backend.agentcore.cli_config import resolve_agentcore_config

        workload_identity = resolve_agentcore_config().workload_identity
    except Exception:
        workload_identity = os.getenv("AGENTCORE_WORKLOAD_IDENTITY")
    if workload_identity:
        bind(
            db,
            provider="agentcore_workload",
            subject_id=workload_identity,
            principal=principal,
        )


if __name__ == "__main__":
    main()
