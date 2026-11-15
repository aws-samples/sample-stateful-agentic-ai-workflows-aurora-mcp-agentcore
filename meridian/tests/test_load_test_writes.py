"""The load test's catalog scenario writes no rows: each request runs against the real app."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import main as backend_main
from backend.agentcore import identity
from backend.authorization import AuthorizationContext
from backend.db import rds_data_client
from scripts import load_test

WRITE_VERBS = {"INSERT", "UPDATE", "DELETE"}


class RecordingRdsData:
    """A boto3 rds-data stand-in that records statements and answers the grant lookup."""

    def __init__(self):
        self.statements: list[str] = []

    def begin_transaction(self, **_kwargs):
        return {"transactionId": "tx"}

    def commit_transaction(self, **_kwargs):
        return {}

    def rollback_transaction(self, **_kwargs):
        return {}

    def execute_statement(self, **kwargs):
        sql = " ".join(kwargs["sql"].split())
        self.statements.append(sql)
        if "FROM traveler_identity_bindings" in sql:
            return {"columnMetadata": [{"name": "binding_id"}],
                    "records": [[{"stringValue": "bind_1"}]]}
        return {"columnMetadata": [], "records": []}

    def writes(self) -> list[str]:
        return [s for s in self.statements if s.split()[0].upper() in WRITE_VERBS]


@pytest.fixture
def app_over_recording_client(monkeypatch):
    fake = RecordingRdsData()
    client = rds_data_client.RDSDataClient()
    client.client = fake
    monkeypatch.setattr(rds_data_client, "_client", client)
    adapter = identity.get_agentcore_identity()
    monkeypatch.setattr(
        adapter, "authorization_context",
        lambda: AuthorizationContext(
            provider="aws_iam", subject_id="AROAFAKE",
            principal="arn:aws:iam::123456789012:role/fake"),
    )
    return fake, TestClient(backend_main.app, raise_server_exceptions=False)


def test_every_catalog_request_leaves_the_database_unwritten(app_over_recording_client):
    fake, client = app_over_recording_client

    for req in load_test.build_requests("catalog", "trv_meridian_demo"):
        reply = client.get(req.path)

        assert reply.status_code == 200, req.name
        assert fake.writes() == [], f"{req.name} wrote: {fake.writes()}"


@pytest.mark.parametrize("path", ["/api/memory/trv_meridian_demo", "/api/journeys?limit=5"])
def test_the_scoped_reads_write_an_audit_row_so_they_stay_out_of_the_allowlist(
    app_over_recording_client, path,
):
    fake, client = app_over_recording_client

    assert client.get(path).status_code == 200
    assert any("traveler_access_audit" in sql for sql in fake.writes())
    with pytest.raises(load_test.WriteRefused):
        load_test.guard(load_test.Req("scoped", "GET", path))
