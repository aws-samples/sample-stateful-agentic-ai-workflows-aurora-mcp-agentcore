"""A heartbeat renews the lease inside a scoped session that checks the grant but writes no audit row."""
from contextlib import asynccontextmanager

import pytest

from backend.agents.phase_05_workflow import lease as lease_module
from backend.agents.phase_05_workflow.lease import AuroraLeaseStore
from backend.db.journey_store import RENEW_SQL
from backend.db.rds_data_client import RDSDataClient


class RenewClient:
    """Records the scoped session's arguments and the statements run inside it."""

    def __init__(self, renewed: bool = True, fail: bool = False) -> None:
        self.renewed = renewed
        self.fail = fail
        self.sessions: list[dict] = []
        self.statements: list[tuple] = []

    @asynccontextmanager
    async def scoped_session(self, **kwargs):
        self.sessions.append(kwargs)
        yield "tx-1"

    async def execute(self, sql, params=(), transaction_id=None):
        assert transaction_id == "tx-1"
        self.statements.append((sql, params))
        if self.fail:
            raise RuntimeError("Data API throttled")
        return [{"execution_id": params[1]}] if self.renewed else []


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    class Identity:
        def authorization_context(self):
            return "caller"
    monkeypatch.setattr(lease_module, "get_agentcore_identity", lambda: Identity())


async def test_a_renewal_runs_in_a_scoped_session_that_writes_no_audit_row():
    client = RenewClient()
    assert await AuroraLeaseStore(client).renew("trv", "exe_1", 60) is True
    assert client.statements == [(RENEW_SQL, ("60", "exe_1"))]
    assert client.sessions == [{
        "traveler_id": "trv", "agent_type": lease_module.AGENT_TYPE,
        "authorization": "caller", "write_audit": False,
    }]


async def test_the_claim_and_the_release_still_write_the_audit_row():
    client = RenewClient()
    store = AuroraLeaseStore(client)
    async with store._scoped("trv"):
        pass
    assert client.sessions[-1]["write_audit"] is True


async def test_a_renewal_of_an_execution_that_no_longer_runs_is_false():
    assert await AuroraLeaseStore(RenewClient(renewed=False)).renew("trv", "exe_1", 60) is False


async def test_a_failed_renewal_raises():
    with pytest.raises(RuntimeError, match="throttled"):
        await AuroraLeaseStore(RenewClient(fail=True)).renew("trv", "exe_1", 60)


class ScopedSessionClient(RDSDataClient):
    """The real scoped_session over recorded Data API calls."""

    def __init__(self) -> None:  # noqa: D107 - no AWS client: every call below is replaced
        self.audit_flags: list[bool] = []

    def begin_transaction(self):
        return "tx-1"

    def commit_transaction(self, tx):
        return None

    def rollback_transaction(self, tx):
        return None

    async def check_traveler_authorization(self, traveler_id, authorization, *,
                                           transaction_id=None, write_audit=True):
        self.audit_flags.append(write_audit)

        class Allowed:
            allowed = True
        return Allowed()

    async def _pin_rls_settings(self, tx, traveler_id, agent_type, authorization):
        return None

    async def _assume_app_role(self, tx, app_role, allow_unscoped):
        return None


@pytest.mark.parametrize("write_audit", [True, False])
async def test_scoped_session_passes_write_audit_to_the_grant_check(write_audit):
    client = ScopedSessionClient()
    async with client.scoped_session(traveler_id="trv", authorization="caller",
                                     write_audit=write_audit):
        pass
    assert client.audit_flags == [write_audit]
