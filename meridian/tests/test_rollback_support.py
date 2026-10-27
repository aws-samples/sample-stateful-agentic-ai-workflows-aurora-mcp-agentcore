"""Database tests that write evidence roll it back: no commit on success or on failure."""

from contextlib import nullcontext

import pytest

from backend.authorization import AuthorizationContext, AuthorizationDecision
from backend.db.rds_data_client import RDSDataClient
from tests.rollback_support import uncommitted_scope, uncommitted_transaction

CALLER = AuthorizationContext(
    provider="aws_iam", subject_id="AROATESTROLE",
    principal="arn:aws:sts::123456789012:assumed-role/Meridian/session-a",
)
SCOPE = {"traveler_id": "trv_meridian_demo", "agent_type": "concierge_agent",
         "authorization": CALLER}


class RecordingClient(RDSDataClient):
    """The real scoped_session over a transport that records how each transaction ends."""

    def __init__(self) -> None:
        self.commits: list[str] = []
        self.rollbacks: list[str] = []
        self.statements: list[tuple[str, str | None]] = []

    def begin_transaction(self) -> str:
        return "tx-test"

    def commit_transaction(self, transaction_id: str) -> None:
        self.commits.append(transaction_id)

    def rollback_transaction(self, transaction_id: str) -> None:
        self.rollbacks.append(transaction_id)

    async def execute(self, query, params=None, transaction_id=None):
        self.statements.append((" ".join(query.split()), transaction_id))
        return []

    async def check_traveler_authorization(self, traveler_id, authorization, **_kwargs):
        return AuthorizationDecision(
            allowed=True, decision="allow", traveler_id=traveler_id,
            provider=authorization.provider, subject_id=authorization.subject_id,
            principal=authorization.principal,
        )


async def test_a_scoped_block_that_finishes_is_rolled_back_not_committed():
    client = RecordingClient()

    async with uncommitted_scope(client, **SCOPE) as tx:
        await client.execute("INSERT INTO agent_audit_log VALUES (1)", transaction_id=tx)

    assert (client.commits, client.rollbacks) == ([], ["tx-test"])
    assert ("INSERT INTO agent_audit_log VALUES (1)", "tx-test") in client.statements
    assert any("SET LOCAL ROLE" in sql for sql, _ in client.statements), "RLS must apply"


async def test_a_scoped_block_that_fails_is_rolled_back_and_the_failure_is_kept():
    client = RecordingClient()

    with pytest.raises(AssertionError, match="the row was wrong"):
        async with uncommitted_scope(client, **SCOPE):
            raise AssertionError("the row was wrong")

    assert (client.commits, client.rollbacks) == ([], ["tx-test"])


@pytest.mark.parametrize("fails", [False, True])
async def test_a_plain_transaction_is_rolled_back_either_way(fails):
    client = RecordingClient()

    with pytest.raises(AssertionError) if fails else nullcontext():
        async with uncommitted_transaction(client) as tx:
            assert tx == "tx-test"
            assert not fails

    assert (client.commits, client.rollbacks) == ([], ["tx-test"])
