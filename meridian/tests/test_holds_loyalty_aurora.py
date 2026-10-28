"""loyalty_balance runs under the gateway login's grant and the traveler's RLS, against Aurora."""

import importlib.util
import os
import sys

import boto3
import pytest

from backend.agentcore.identity import get_agentcore_identity
from backend.db.rds_data_client import get_rds_data_client
from backend.tools import package_tools
from tests.lambda_targets_support import TARGETS
from tests.live_support import (
    DECOY,
    JORDAN,
    live_region,
    require_bound_to_jordan_only,
    require_env,
    saved_program,
)

pytestmark = pytest.mark.database


@pytest.fixture
def holds(monkeypatch):
    """The holds Lambda's module, wired to the gateway login and this machine's workload."""
    require_env("AURORA_GATEWAY_SECRET_ARN")
    spec = importlib.util.spec_from_file_location(
        "holds_loyalty_live", TARGETS / "meridian_holds" / "lambda_function.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    caller = get_agentcore_identity().authorization_context()
    monkeypatch.setattr(module, "CONFIG", module.AuroraConfig(
        os.environ["AURORA_CLUSTER_ARN"], os.environ["AURORA_GATEWAY_SECRET_ARN"],
        os.getenv("AURORA_DATABASE", "meridian")))
    monkeypatch.setattr(module, "SUBJECT", (caller.provider, caller.subject_id, caller.principal))
    monkeypatch.setattr(module, "RDS", boto3.client("rds-data", region_name=live_region()))
    return module


async def test_the_bound_traveler_reads_the_balance_aurora_holds(holds):
    await require_bound_to_jordan_only()
    saved = await saved_program(JORDAN)

    result = holds.loyalty_balance({"travelerId": JORDAN, "program": saved["program"]})

    assert result["points_balance"] == int(saved["points_balance"])
    assert result["tier"] == (saved.get("tier") or "Member")
    assert result["governance"]["decision"] == "allow"


async def test_a_traveler_the_workload_is_not_bound_to_is_refused_and_a_deny_row_is_written(holds):
    subject = await require_bound_to_jordan_only()

    result = holds.loyalty_balance({"travelerId": DECOY, "program": "Marriott Bonvoy"})

    assert result["error"] == "traveler_not_authorized"
    assert "points_balance" not in result
    assert result["governance"]["decision"] == "deny"
    kept = await get_rds_data_client().execute(
        "SELECT decision, requested_traveler_id, subject_id FROM traveler_access_audit "
        "WHERE audit_id = %s", (result["governance"]["access_audit_id"],))
    assert kept == [{"decision": "deny", "requested_traveler_id": DECOY, "subject_id": subject}]


async def test_the_in_process_package_details_equal_the_gateway_tool_and_aurora(holds):
    master = get_rds_data_client()
    package_id = (await master.execute(
        "SELECT package_id FROM trip_packages ORDER BY package_id LIMIT 1"))[0]["package_id"]

    in_process = await package_tools.get_package_details(package_id)
    gateway_tool = holds.get_package_details({"packageId": package_id})
    stored = (await master.execute(
        "SELECT price_per_person, package_places_left(package_id) AS availability "
        "FROM trip_packages WHERE package_id = %s", (package_id,)))[0]

    for answer in (in_process, gateway_tool):
        assert answer["package"]["availability"] == stored["availability"]
        assert float(answer["package"]["price_per_person"]) == float(stored["price_per_person"])
    assert in_process["summary"] == gateway_tool["summary"]
