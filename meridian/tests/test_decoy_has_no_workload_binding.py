"""Decision: the decoy never gets an aws_iam workload binding, so it is refused at the grant too.

The Cognito binding makes a person their traveler. A workload binding would make the App Runner
role, the Lambdas or the Runtimes act for the decoy. Leaving it out keeps the decoy refused on its
own records at the workload grant as well as at RLS (docs/OPERATIONS.md, Sign-in and who is
calling). The static test pins the scripts, tests/test_bind_refuses_the_decoy.py pins the
shared guard they all go through, and the database test pins the cluster.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.db.rds_data_client import get_rds_data_client

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
DECOY = "trv_demo_decoy"


GUARD = "bind_current_identity.py"  # names the decoy only to refuse it; behavior is tested
SCRIPTS_TO_GREP = sorted(p for p in SCRIPTS.glob("bind_*.py") if p.name != GUARD)


@pytest.mark.parametrize("script", SCRIPTS_TO_GREP, ids=lambda p: p.name)
def test_no_script_binds_a_workload_to_the_decoy(script):
    assert DECOY not in script.read_text(encoding="utf-8")


def test_there_is_a_binding_script_for_every_workload_and_they_were_all_checked():
    names = {path.name for path in SCRIPTS.glob("bind_*.py")}

    assert {"bind_gateway_workload.py", "bind_web_backend_role.py",
            "bind_workflow_runtime.py", "bind_current_identity.py"} <= names


@pytest.mark.database
async def test_the_cluster_has_no_active_workload_binding_for_the_decoy():
    rows = await get_rds_data_client().execute(
        "SELECT count(*) AS n FROM traveler_identity_bindings "
        "WHERE traveler_id = %s AND identity_provider = 'aws_iam' AND status = 'active'",
        (DECOY,))

    assert rows[0]["n"] == 0
