"""Cross-traveler counts reach the evidence endpoints through one definer function.

The backend login is subject to RLS, so a plain COUNT outside a traveler scope would silently
report zero. These tests pin that every unscoped total goes through ``backend_admin_count`` and
that no unscoped SELECT over a traveler table remains in the two endpoints.
"""

import contextlib
from unittest.mock import AsyncMock, Mock

import pytest

from backend.authorization import AuthorizationContext, AuthorizationDecision
from backend.db.admin_counts import RLS_BASELINE_KINDS, admin_count
from backend.http_auth import HttpPrincipal
from backend.routers import diagnostics

PRINCIPAL = HttpPrincipal("subject", "trv_meridian_demo", "test")
IDENTITY = Mock(authorization_context=lambda: AuthorizationContext("aws_iam", "s", "p"))


class RecordingDb:
    """Answers each statement by its shape and records what was asked."""

    def __init__(self, *, scoped=17, baseline=22, missing=()):
        self.statements = []
        self.scoped, self.baseline, self.missing = scoped, baseline, set(missing)
        self.check_traveler_authorization = AsyncMock(side_effect=self._decision)

    async def _decision(self, traveler_id, authorization, **_):
        return AuthorizationDecision(
            allowed=True, decision="allow", traveler_id=traveler_id, provider="aws_iam",
            subject_id="s", principal="p", reason="active identity binding",
        )

    @contextlib.asynccontextmanager
    async def scoped_session(self, **_):
        yield "tx-1"

    async def execute(self, sql, params=(), transaction_id=None):
        self.statements.append((" ".join(sql.split()), params, transaction_id))
        if "backend_admin_count" in sql:
            kind = params[0]
            if kind in self.missing:
                raise RuntimeError("function backend_admin_count does not exist")
            return [{"n": self.baseline}]
        if "to_regclass" in sql:
            return [{"present": "workflow_snapshots" not in self.missing}]
        if "pg_policies" in sql:
            return []
        if "current_user" in sql:
            return [{"effective_role": "meridian_app", "rls_active": True, "scope": "x",
                     "auth_provider": "aws_iam", "auth_subject": "s"}]
        return [{"n": self.scoped}]

    def unscoped(self):
        return [s for s in self.statements if s[2] is None]


@pytest.fixture
def db(monkeypatch):
    fake = RecordingDb()
    monkeypatch.setattr(diagnostics, "get_rds_data_client", lambda: fake)
    monkeypatch.setattr(diagnostics, "get_agentcore_identity", lambda: IDENTITY)
    return fake


async def test_admin_count_calls_the_definer_function_with_its_arguments():
    fake = RecordingDb(baseline=7)
    assert await admin_count(fake, "audit_deny", window="90 minutes") == 7
    assert fake.statements == [(
        "SELECT backend_admin_count(%s, %s::interval, %s) AS n",
        ("audit_deny", "90 minutes", None), None)]


async def test_admin_count_treats_an_empty_answer_as_zero():
    class Empty:
        async def execute(self, sql, params=()):
            return [{"n": None}]

    assert await admin_count(Empty(), "agent_audit", window="1 minutes") == 0


async def test_every_probe_table_has_a_baseline_kind():
    assert set(RLS_BASELINE_KINDS) == set(diagnostics.ALLOWED_TABLES)


async def test_the_rls_probe_baseline_comes_from_the_definer_function(db):
    request = diagnostics.RlsProbeRequest(tables=["traveler_preferences", "conversations"])
    response = await diagnostics.rls_probe(request, PRINCIPAL)
    assert [(t.table, t.scoped_count, t.unscoped_count) for t in response.tables] == [
        ("traveler_preferences", 17, 22), ("conversations", 17, 22)]
    kinds = [params[0] for sql, params, _ in db.unscoped() if "backend_admin_count" in sql]
    assert kinds == ["rls_traveler_preferences", "rls_conversations"]
    assert not [s for s in db.unscoped() if s[0].startswith("SELECT COUNT(*)")]


async def test_the_rls_probe_defaults_to_the_authenticated_traveler(db):
    response = await diagnostics.rls_probe(diagnostics.RlsProbeRequest(), PRINCIPAL)
    assert response.traveler_id == "trv_meridian_demo"
    decoy = HttpPrincipal("subject", "trv_demo_decoy", "test")
    response = await diagnostics.rls_probe(diagnostics.RlsProbeRequest(), decoy)
    assert response.traveler_id == "trv_demo_decoy"


async def test_the_session_receipt_counts_governance_through_the_function(db):
    request = diagnostics.SessionReceiptRequest(conversation_id="thread-1", window_minutes=30)
    response = await diagnostics.session_receipt(request, PRINCIPAL)
    calls = [(p[0], p[1], p[2]) for s, p, t in db.unscoped() if "backend_admin_count" in s]
    assert calls == [
        ("audit_allow", "30 minutes", None), ("audit_deny", "30 minutes", None),
        ("agent_audit", "30 minutes", None), ("workflow_snapshots", None, "thread-1")]
    direct = [s for s in db.unscoped() if " FROM traveler_access_audit" in s[0]
              or " FROM agent_audit_log" in s[0] or " FROM workflow_snapshots" in s[0]]
    assert direct == []
    lines = {line.table: line for line in response.lines}
    assert lines["traveler_access_audit"].count == 44
    assert lines["workflow_snapshots"].count == 22
    assert response.durable_checkpoints is True


async def test_a_receipt_without_a_thread_counts_no_snapshots(db):
    response = await diagnostics.session_receipt(diagnostics.SessionReceiptRequest(), PRINCIPAL)
    assert not [s for s in db.statements if s[1] and s[1][0] == "workflow_snapshots"]
    line = next(line for line in response.lines if line.table == "workflow_snapshots")
    assert line.count == 0 and "no workflow thread" in line.detail
    assert response.durable_checkpoints is False


async def test_a_missing_snapshot_table_is_reported_not_counted(monkeypatch):
    fake = RecordingDb(missing=("workflow_snapshots",))
    monkeypatch.setattr(diagnostics, "get_rds_data_client", lambda: fake)
    monkeypatch.setattr(diagnostics, "get_agentcore_identity", lambda: IDENTITY)
    request = diagnostics.SessionReceiptRequest(conversation_id="thread-1")
    response = await diagnostics.session_receipt(request, PRINCIPAL)
    line = next(line for line in response.lines if line.table == "workflow_snapshots")
    assert line.count == 0 and "no workflow snapshot table" in line.detail


async def test_an_unavailable_count_degrades_its_line_and_not_the_receipt(monkeypatch):
    fake = RecordingDb(missing=("audit_allow", "audit_deny"))
    monkeypatch.setattr(diagnostics, "get_rds_data_client", lambda: fake)
    monkeypatch.setattr(diagnostics, "get_agentcore_identity", lambda: IDENTITY)
    response = await diagnostics.session_receipt(diagnostics.SessionReceiptRequest(), PRINCIPAL)
    line = next(line for line in response.lines if line.table == "traveler_access_audit")
    assert line.count == 0 and line.detail == "0 allow, 0 deny"
