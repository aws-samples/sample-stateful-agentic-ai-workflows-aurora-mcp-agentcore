"""Authorization evidence must be the decision that admitted the run, not a later read.

Every scoped read, including the journey read itself, inserts an ``allow`` row
into ``traveler_access_audit``. The latest row for the traveler is therefore
always a recent read. The evidence is bounded by the moment the run was
admitted: its latest execution's start, or the journey's creation when no worker
has claimed it. The SQL is verified against Aurora in ``test_journey_document.py``.
"""

import pytest

from backend.db.journey_document import _authorization, _authorization_bound


def test_a_run_is_judged_by_the_decision_that_admitted_its_latest_execution():
    executions = {"status": "observed", "items": [
        {"started_at": "2026-10-02 13:36:33+00"},
        {"started_at": "2026-10-02 14:00:00+00"},
    ]}

    bound = _authorization_bound({"created_at": "2026-10-02 13:36:30+00"}, executions)

    assert bound == "2026-10-02T14:00:00+00:00"


def test_a_journey_no_worker_has_claimed_is_judged_at_its_creation():
    unclaimed = {"status": "unavailable", "reason": "no execution has claimed this journey yet"}

    bound = _authorization_bound({"created_at": "2026-10-02 13:36:30+00"}, unclaimed)

    assert bound == "2026-10-02T13:36:30+00:00"


@pytest.mark.asyncio
async def test_the_decision_is_read_for_this_traveler_up_to_the_bound():
    seen = []

    async def q(sql, params):
        seen.append(params)
        return []

    await _authorization(q, "trv_1", "2026-10-02T14:00:00+00:00")

    assert seen == [("trv_1", "2026-10-02T14:00:00+00:00")]


@pytest.mark.asyncio
async def test_no_decision_before_the_run_reports_unavailable():
    async def q(sql, params):
        return []

    evidence = await _authorization(q, "trv_1", "2026-10-02T14:00:00+00:00")

    assert evidence["status"] == "unavailable"
    assert evidence["source"] == "traveler_access_audit"
    assert evidence["reason"]


@pytest.mark.asyncio
async def test_an_earlier_decision_is_reported_as_observed():
    async def q(sql, params):
        return [{
            "audit_id": "authz_1", "identity_provider": "aws_iam", "subject_id": "AROA",
            "principal": "arn:aws:sts::1:assumed-role/Meridian/s", "decision": "allow",
            "reason": "active identity binding", "decided_at": "2026-10-01 10:00:00",
        }]

    evidence = await _authorization(q, "trv_1", "2026-10-02T14:00:00+00:00")

    assert evidence["status"] == "observed"
    assert evidence["audit_id"] == "authz_1"
    assert evidence["observed_at"] == "2026-10-01T10:00:00+00:00"
