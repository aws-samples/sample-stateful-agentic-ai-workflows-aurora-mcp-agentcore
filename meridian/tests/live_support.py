"""Shared preconditions for the database-marked tests that read Amazon Aurora back."""

import os

import pytest

from backend.agentcore.identity import get_agentcore_identity
from backend.db.rds_data_client import get_rds_data_client

JORDAN = "trv_meridian_demo"
DECOY = "trv_demo_decoy"
MASTER_ENV = ("AURORA_CLUSTER_ARN", "AURORA_SECRET_ARN")


def require_env(*names: str) -> None:
    """Fail the test, naming every missing variable at once."""
    missing = [name for name in (*MASTER_ENV, *names) if not os.environ.get(name)]
    if missing:
        pytest.fail(
            f"live tests need {', '.join(missing)}; the login secrets come from "
            "scripts/provision_service_logins.py")


def live_region() -> str:
    """The Region every live client uses."""
    return os.getenv("AGENTCORE_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"


def laptop_subject() -> str:
    """The IAM role id this machine's workload is bound by."""
    return get_agentcore_identity().authorization_context().subject_id


async def require_bound_to_jordan_only() -> str:
    """Fail unless this workload has an active binding to Jordan and none to the decoy.

    Returns:
        The workload's subject id.
    """
    subject = laptop_subject()
    rows = await get_rds_data_client().execute(
        "SELECT traveler_id FROM traveler_identity_bindings WHERE identity_provider = 'aws_iam' "
        "AND subject_id = %s AND status = 'active' "
        "AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)", (subject,))
    bound = sorted(row["traveler_id"] for row in rows)
    if JORDAN not in bound or DECOY in bound:
        pytest.fail(
            f"the workload (subject ending {subject[-4:]}) must be bound to {JORDAN} and not to "
            f"{DECOY}; it holds active bindings to {bound}")
    return subject


async def saved_program(traveler: str) -> dict:
    """The traveler's first saved loyalty program that has a name and a balance.

    Fails, rather than skips, when there is none: both travelers are seeded with programs.
    """
    rows = await get_rds_data_client().execute(
        "SELECT loyalty_programs FROM traveler_profiles WHERE traveler_id = %s", (traveler,))
    programs = (rows[0]["loyalty_programs"] if rows else None) or {}
    for value in programs.values():
        if isinstance(value, dict) and value.get("program") and (
                value.get("points_balance") is not None):
            return value
    pytest.fail(f"{traveler} has no saved loyalty program with a balance: the seed is missing")


async def access_audit_ids(subject: str, traveler: str, decision: str) -> set[str]:
    """Audit ids of this workload's rows for one traveler and decision."""
    rows = await get_rds_data_client().execute(
        "SELECT audit_id FROM traveler_access_audit WHERE subject_id = %s "
        "AND requested_traveler_id = %s AND decision = %s", (subject, traveler, decision))
    return {row["audit_id"] for row in rows}
