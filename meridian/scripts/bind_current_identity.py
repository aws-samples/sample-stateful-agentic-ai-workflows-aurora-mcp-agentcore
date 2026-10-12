"""Apply the identity-binding migration and authorize this AWS caller for Jordan."""

from __future__ import annotations

import argparse
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
DEMO_TRAVELER_ID = "trv_meridian_demo"
DECOY_TRAVELER_ID = "trv_demo_decoy"
SEEDED_TRAVELERS = (DEMO_TRAVELER_ID, DECOY_TRAVELER_ID)
MIGRATION = Path(__file__).resolve().parent / "migrations" / "005_bind_identity_to_traveler.sql"


def refuse_decoy(traveler_id: str, *, allow_decoy: bool) -> None:
    """Refuse a decoy binding unless the caller is one of the two entry points that may make it.

    Only ``bind_web_backend_role.py`` and ``bind_workflow_runtime.py`` set ``allow_decoy``.
    The Gateway holds role and this laptop identity stay Jordan Morgan only.
    """
    if traveler_id == DECOY_TRAVELER_ID and not allow_decoy:
        raise SystemExit(
            f"Refusing to bind a workload to {DECOY_TRAVELER_ID}: only bind_web_backend_role.py "
            "and bind_workflow_runtime.py bind the decoy (--traveler). The Gateway holds role "
            "and this laptop identity stay Jordan Morgan only (docs/OPERATIONS.md, Sign-in and "
            "who is calling). Unset MERIDIAN_DEMO_TRAVELER_ID or set it to trv_meridian_demo."
        )


def add_traveler_arguments(parser: argparse.ArgumentParser) -> None:
    """Add ``--traveler`` and ``--apply`` to a binding script that may bind the decoy."""
    parser.add_argument(
        "--traveler", choices=SEEDED_TRAVELERS, default=DEMO_TRAVELER_ID,
        help="the seeded Cognito traveler to bind (default: %(default)s, Jordan Morgan)")
    parser.add_argument(
        "--apply", action="store_true",
        help="write the binding; without it the script only prints what it would bind")


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
    traveler_id: str,
    provider: str,
    subject_id: str,
    principal: str,
    allow_decoy: bool = False,
) -> None:
    """Write one active binding of a workload to ``traveler_id``.

    Raises:
        SystemExit: The traveler is the decoy and the caller did not pass ``allow_decoy``.
    """
    refuse_decoy(traveler_id, allow_decoy=allow_decoy)
    digest = hashlib.sha256(
        f"{provider}:{subject_id}:{traveler_id}".encode()
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
            {"name": "traveler_id", "value": {"stringValue": traveler_id}},
            {"name": "principal", "value": {"stringValue": principal}},
        ],
    )
    console.print(
        f"[green]Authorized a {provider} workload for {traveler_id}[/green]"
    )


def main() -> None:
    refuse_decoy(TRAVELER_ID, allow_decoy=False)
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
        traveler_id=TRAVELER_ID,
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
            traveler_id=TRAVELER_ID,
            provider="agentcore_workload",
            subject_id=workload_identity,
            principal=principal,
        )


if __name__ == "__main__":
    main()
