"""Authorization evidence must be a decision made before this read, not the read's own row.

``scoped_session`` inserts an ``allow`` row into ``traveler_access_audit`` inside
the same transaction as the journey read, and ``decided_at`` defaults to
``CURRENT_TIMESTAMP``, which is the transaction start. Selecting the latest row
for the traveler therefore returns the row this read just wrote. The SQL
semantics are verified against Aurora in ``test_journey_document.py``; these
cover the parts that do not need a database.
"""

import pytest

from backend.db.journey_document import AUDIT_SQL, _authorization


def test_the_audit_query_excludes_rows_written_by_the_reading_transaction():
    sql = " ".join(AUDIT_SQL.split())
    assert "decided_at < CURRENT_TIMESTAMP" in sql
    assert "requested_traveler_id = %s" in sql
    assert "ORDER BY decided_at DESC LIMIT 1" in sql


@pytest.mark.asyncio
async def test_no_earlier_decision_reports_unavailable_not_the_current_read():
    async def q(sql, params):
        return []

    evidence = await _authorization(q, "trv_1")

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

    evidence = await _authorization(q, "trv_1")

    assert evidence["status"] == "observed"
    assert evidence["audit_id"] == "authz_1"
    assert evidence["observed_at"] == "2026-10-01T10:00:00+00:00"
