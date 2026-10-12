"""Decision: the Gateway holds role stays Jordan Morgan only; the decoy has its own data elsewhere.

Jordan Lee (``trv_demo_decoy``) uses the app with their own records, so the App Runner role and
the MeridianWorkflow Runtime role are bound to the decoy. The holds Lambda is not: holds stay
Jordan Morgan's, and the recorded proof's Gateway row (the decoy refused at the holds grant)
depends on it. The static tests pin the scripts, tests/test_bind_refuses_the_decoy.py pins the
shared rule they go through, and the database test pins the cluster
(docs/OPERATIONS.md, Sign-in and who is calling).
"""

from __future__ import annotations

import os
from pathlib import Path

import boto3
import pytest

from backend.db.rds_data_client import get_rds_data_client
from scripts.bind_gateway_workload import DEFAULT_FUNCTION, role_subject

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
DECOY = "trv_demo_decoy"


def test_the_gateway_binding_script_never_names_the_decoy():
    assert DECOY not in (SCRIPTS / "bind_gateway_workload.py").read_text(encoding="utf-8")


def test_there_is_a_binding_script_for_every_workload_and_they_were_all_checked():
    names = {path.name for path in SCRIPTS.glob("bind_*.py")}

    assert {"bind_gateway_workload.py", "bind_web_backend_role.py",
            "bind_workflow_runtime.py", "bind_current_identity.py"} <= names


def gateway_holds_role() -> tuple[str, str]:
    """The holds Lambda's (RoleId, role ARN), read the way bind_gateway_workload reads them."""
    region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    role_arn = boto3.client("lambda", region_name=region).get_function_configuration(
        FunctionName=DEFAULT_FUNCTION)["Role"]
    return role_subject(boto3.client("iam"), role_arn)


@pytest.mark.database
async def test_the_cluster_has_no_active_decoy_binding_for_the_gateway_holds_role():
    subject_id, role_arn = gateway_holds_role()

    rows = await get_rds_data_client().execute(
        "SELECT count(*) AS n FROM traveler_identity_bindings "
        "WHERE traveler_id = %s AND identity_provider = 'aws_iam' AND status = 'active' "
        "AND (subject_id = %s OR granted_by = %s)",
        (DECOY, subject_id, role_arn))

    assert rows[0]["n"] == 0
