"""A worker's lease is bound to the journey the hold targets, on the live cluster."""

import uuid

import pytest

from backend.db.journey_store import claim_execution
from backend.db.rds_data_client import get_rds_data_client
from tests import test_gateway_login_aurora as login
from tests.test_gateway_login_aurora import (
    JORDAN,
    _hold_args,
    _package_with_room,
    _remove,
    _saved_cap_cents,
)

holds = login.holds

pytestmark = pytest.mark.database


async def _journey_with_hold(holds, journey_ref, package):
    package_id, duration, cents = package
    held = holds.create_courtesy_hold({
        **_hold_args(package_id, duration, cents, JORDAN, journey_ref),
        "holdRequestId": f"hrq_{uuid.uuid4().hex[:12]}",
    })
    assert "error" not in held, held
    return held["hold"]


async def test_a_live_lease_of_journey_b_cannot_create_a_hold_on_journey_a(holds):
    master = get_rds_data_client()
    package = await _package_with_room(within_cents=await _saved_cap_cents(JORDAN))
    ref_a, ref_b = (f"leasejrn-{uuid.uuid4().hex[:10]}" for _ in range(2))
    attempted = f"hrq_{uuid.uuid4().hex[:12]}"
    try:
        hold_a = await _journey_with_hold(holds, ref_a, package)
        hold_b = await _journey_with_hold(holds, ref_b, package)
        assert hold_a["journeyId"] != hold_b["journeyId"]
        claim = await claim_execution(master, hold_b["journeyId"], ref_b, "worker-b", 30)
        assert claim.claimed
        package_id, duration, cents = package
        result = holds.create_courtesy_hold({
            **_hold_args(package_id, duration, cents, JORDAN, ref_a),
            "holdRequestId": attempted, "executionId": claim.execution_id,
        })
        assert result == {"error": "journey_not_owned"}
        written = await master.execute(
            "SELECT booking_id FROM hold_requests WHERE request_id = %s", (attempted,))
        assert written == [], "the mismatched call wrote no hold request"
        valid = holds.create_courtesy_hold({
            **_hold_args(package_id, duration, cents, JORDAN, ref_b),
            "holdRequestId": f"hrq_{uuid.uuid4().hex[:12]}", "executionId": claim.execution_id,
        })
        assert "error" not in valid, valid
        assert valid["hold"]["journeyId"] == hold_b["journeyId"]
    finally:
        for ref in (ref_a, ref_b):
            await master.execute("DELETE FROM journey_executions WHERE thread_id = %s", (ref,))
            await _remove("", ref)
