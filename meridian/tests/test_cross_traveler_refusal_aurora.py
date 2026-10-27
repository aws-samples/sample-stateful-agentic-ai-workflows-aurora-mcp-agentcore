"""A refused cross-traveler request as Aurora keeps it, written and checked without committing.

Run as the master login in `.env` (it reads traveler_access_audit, which the backend login may
only insert into). Each test uses a traveler id no real row names, and its own transaction, which
is rolled back: the deny row is checked inside that transaction and never committed.
"""

import uuid

import pytest
from fastapi import HTTPException

from backend import http_auth
from backend.db.journey_document import _authorization
from backend.db.rds_data_client import get_rds_data_client
from backend.http_auth import HttpPrincipal, authorize_traveler
from tests.rollback_support import uncommitted_transaction

pytestmark = pytest.mark.database

DECOY = HttpPrincipal("sub-itest-decoy", "trv_demo_decoy", "cognito")
ROWS_SQL = (
    "SELECT identity_provider, subject_id, principal, requested_traveler_id, decision, reason "
    "FROM traveler_access_audit WHERE requested_traveler_id = %s"
)


class InTransaction:
    """The real writer, bound to the test's transaction so its row is rolled back."""

    def __init__(self, client, tx: str) -> None:
        self.client, self.tx = client, tx

    async def record_traveler_access(self, **row):
        return await self.client.record_traveler_access(**row, transaction_id=self.tx)


async def refuse(monkeypatch, client, tx, requested: str) -> None:
    monkeypatch.setattr(http_auth, "get_rds_data_client", lambda: InTransaction(client, tx))
    with pytest.raises(HTTPException) as refused:
        await authorize_traveler(DECOY, requested)
    assert refused.value.status_code == 403


async def test_a_refused_request_leaves_exactly_one_deny_row(monkeypatch):
    client = get_rds_data_client()
    requested = f"trv_itest_{uuid.uuid4().hex[:8]}"
    async with uncommitted_transaction(client) as tx:
        await refuse(monkeypatch, client, tx, requested)
        rows = await client.execute(ROWS_SQL, (requested,), transaction_id=tx)

    assert rows == [{
        "identity_provider": "cognito", "subject_id": "sub-itest-decoy",
        "principal": "traveler:trv_demo_decoy", "requested_traveler_id": requested,
        "decision": "deny", "reason": "requested traveler is not the signed-in traveler",
    }]


async def test_an_over_long_id_is_kept_cut_to_the_column_not_a_server_error(monkeypatch):
    client = get_rds_data_client()
    requested = f"trv_itest_{uuid.uuid4().hex[:8]}" + "x" * 60
    async with uncommitted_transaction(client) as tx:
        await refuse(monkeypatch, client, tx, requested)
        rows = await client.execute(ROWS_SQL, (requested[:50],), transaction_id=tx)

    assert [row["requested_traveler_id"] for row in rows] == [requested[:50]]
    assert rows[0]["reason"].endswith(f"truncated to 50 of {len(requested)} characters")


async def test_a_refusal_after_the_admission_is_not_shown_as_the_runs_evidence(monkeypatch):
    client = get_rds_data_client()
    traveler = f"trv_itest_{uuid.uuid4().hex[:8]}"
    admitted = f"authz_t{uuid.uuid4().hex[:10]}"
    async with uncommitted_transaction(client) as tx:
        await client.execute(
            "INSERT INTO traveler_access_audit (audit_id, identity_provider, subject_id, "
            "principal, requested_traveler_id, decision, reason, decided_at) VALUES "
            "(%s, 'aws_iam', 'AROAITEST', 'arn:aws:sts::123456789012:assumed-role/t/s', %s, "
            "'allow', 'active identity binding', CURRENT_TIMESTAMP - INTERVAL '1 minute')",
            (admitted, traveler), transaction_id=tx)
        await refuse(monkeypatch, client, tx, traveler)
        (bound,) = await client.execute(
            "SELECT (CURRENT_TIMESTAMP + INTERVAL '1 second')::TEXT AS at", transaction_id=tx)

        async def q(sql, params):
            return await client.execute(sql, params, transaction_id=tx)

        evidence = await _authorization(q, traveler, bound["at"])

    assert evidence["audit_id"] == admitted and evidence["decision"] == "allow"
