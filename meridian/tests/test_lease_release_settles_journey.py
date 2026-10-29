"""A worker that lost its lease cannot overwrite the journey status of its successor."""

import pytest

from backend.agents.phase_05_workflow import lease as lease_module
from backend.agents.phase_05_workflow.lease import JOURNEY_SETTLED_SQL, AuroraLeaseStore
from backend.db.journey_store import RELEASE_SQL


class FakeAurora:
    """Holds one execution status and one journey status; mimics the guarded updates."""

    def __init__(self, execution_status: str, journey_status: str) -> None:
        self.execution_status = execution_status
        self.journey_status = journey_status

    def scoped_session(self, **kwargs):
        client = self

        class Session:
            async def __aenter__(self):
                return "tx-1"

            async def __aexit__(self, *exc):
                return False

        assert client is self
        return Session()

    async def execute(self, sql, params=(), transaction_id=None):
        if sql == RELEASE_SQL:
            if self.execution_status != "running":
                return []
            self.execution_status = params[0]
            return [{"execution_id": params[1]}]
        assert sql == JOURNEY_SETTLED_SQL
        self.journey_status = params[0]
        return []


@pytest.fixture(autouse=True)
def identity(monkeypatch):
    class Identity:
        def authorization_context(self):
            return None

    monkeypatch.setattr(lease_module, "get_agentcore_identity", lambda: Identity())


async def test_releasing_an_abandoned_execution_leaves_the_journey_status():
    db = FakeAurora(execution_status="abandoned", journey_status="succeeded")
    await AuroraLeaseStore(db).release("trv", "jrn_1", "exe_1", "failed")
    assert db.journey_status == "succeeded"


async def test_releasing_a_running_execution_settles_the_journey():
    db = FakeAurora(execution_status="running", journey_status="running")
    await AuroraLeaseStore(db).release("trv", "jrn_1", "exe_1", "succeeded")
    assert (db.execution_status, db.journey_status) == ("succeeded", "succeeded")
