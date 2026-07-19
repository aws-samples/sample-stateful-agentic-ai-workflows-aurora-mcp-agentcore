"""Authorization checks that must run before an RLS traveler scope is set."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from backend.agentcore.identity import AgentCoreIdentityAdapter
from backend.authorization import (
    AuthorizationContext,
    AuthorizationDecision,
    TravelerAuthorizationError,
)
from backend.db.rds_data_client import RDSDataClient


AUTHORIZATION = AuthorizationContext(
    provider="aws_iam",
    subject_id="AROATESTROLE",
    principal="arn:aws:sts::123456789012:assumed-role/Meridian/session-a",
)


class _ScopedDb(RDSDataClient):
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.executed: list[str] = []
        self.commits: list[str] = []
        self.rollbacks: list[str] = []

    def begin_transaction(self) -> str:
        return "tx-authz"

    def commit_transaction(self, transaction_id: str) -> None:
        self.commits.append(transaction_id)

    def rollback_transaction(self, transaction_id: str) -> None:
        self.rollbacks.append(transaction_id)

    async def execute(self, query, params=None, transaction_id=None):
        self.executed.append(" ".join(query.split()))
        return []

    async def check_traveler_authorization(
        self,
        traveler_id,
        authorization,
        *,
        transaction_id=None,
        write_audit=True,
    ):
        return AuthorizationDecision(
            allowed=self.allowed,
            decision="allow" if self.allowed else "deny",
            traveler_id=traveler_id,
            provider=authorization.provider,
            subject_id=authorization.subject_id,
            principal=authorization.principal,
            binding_id="bind-demo" if self.allowed else None,
            audit_id="authz-demo",
            reason="active identity binding" if self.allowed else "no active identity binding",
        )


def test_assumed_role_session_uses_stable_iam_subject() -> None:
    adapter = AgentCoreIdentityAdapter(
        workload_identity=None,
        resource_provider=None,
        region="us-east-1",
    )
    adapter._sts = MagicMock()
    adapter._sts.get_caller_identity.return_value = {
        "Arn": "arn:aws:sts::123456789012:assumed-role/Meridian/session-a",
        "UserId": "AROATESTROLE:session-a",
    }

    first = adapter.authorization_context()
    second = adapter.authorization_context()

    assert first.subject_id == "AROATESTROLE"
    assert first.provider == "aws_iam"
    assert second == first
    adapter._sts.get_caller_identity.assert_called_once()


def test_live_agentcore_identity_becomes_authorization_subject() -> None:
    adapter = AgentCoreIdentityAdapter(
        workload_identity="arn:aws:bedrock-agentcore:us-east-1:123:workload-identity/alex",
        resource_provider="meridian-aurora",
        region="us-east-1",
    )
    adapter._sts = MagicMock()
    adapter._sts.get_caller_identity.return_value = {
        "Arn": "arn:aws:sts::123:assumed-role/Meridian/session-a",
        "UserId": "AROATESTROLE:session-a",
    }
    adapter._runtime = MagicMock()
    adapter._runtime.get_resource_api_key.return_value = {"apiKey": "not-retained"}

    scope = adapter.scope_for_turn()

    assert scope.token_status == "live"
    assert scope.authorization.provider == "agentcore_workload"
    assert scope.authorization.subject_id == adapter.workload_identity


def test_scoped_session_rejects_missing_authenticated_subject() -> None:
    db = _ScopedDb(allowed=True)

    async def run() -> None:
        async with db.scoped_session(
            traveler_id="trv_meridian_demo",
            agent_type="memory_agent",
        ):
            pass

    with pytest.raises(RuntimeError, match="AuthorizationContext"):
        asyncio.run(run())
    assert not any("current_traveler_id" in query for query in db.executed)


def test_scoped_session_denies_unbound_traveler_before_setting_rls() -> None:
    db = _ScopedDb(allowed=False)

    async def run() -> None:
        async with db.scoped_session(
            traveler_id="trv_demo_decoy",
            agent_type="memory_agent",
            authorization=AUTHORIZATION,
        ):
            pass

    with pytest.raises(TravelerAuthorizationError):
        asyncio.run(run())
    assert db.commits == ["tx-authz"]  # preserve the DENY audit record
    assert not any("current_traveler_id" in query for query in db.executed)


def test_scoped_session_authorizes_then_sets_rls_scope() -> None:
    db = _ScopedDb(allowed=True)

    async def run() -> None:
        async with db.scoped_session(
            traveler_id="trv_meridian_demo",
            agent_type="concierge_agent",
            authorization=AUTHORIZATION,
        ):
            pass

    asyncio.run(run())

    assert any("app.current_traveler_id" in query for query in db.executed)
    assert any("app.authorization_subject" in query for query in db.executed)
    assert any("SET LOCAL ROLE meridian_app" in query for query in db.executed)
    assert db.commits == ["tx-authz"]
