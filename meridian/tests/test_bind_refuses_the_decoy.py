"""Every bind_*.py entry point refuses to give the decoy traveler an aws_iam workload binding."""

from __future__ import annotations

import importlib
import json
import sys

import pytest

from scripts import bind_current_identity as shared

DECOY = "trv_demo_decoy"
ROLE_ARN = "arn:aws:iam::123456789012:role/Workload"


class Recorder:
    """Stands in for every boto3 client; any rds-data call is recorded."""

    def __init__(self):
        self.calls: list[str] = []

    def execute_statement(self, **_kwargs):
        self.calls.append("execute_statement")

    def get_role(self, **_kwargs):
        return {"Role": {"RoleId": "AROAFAKE", "Arn": ROLE_ARN}}

    def get_function_configuration(self, **_kwargs):
        return {"Role": ROLE_ARN}

    def describe_stacks(self, **_kwargs):
        return {"Stacks": [{"Outputs": [
            {"OutputKey": "InstanceRoleArn", "OutputValue": ROLE_ARN}]}]}

    def get_caller_identity(self):
        return {"Arn": ROLE_ARN, "UserId": "AIDAFAKE:session"}


def test_the_default_traveler_is_the_demo_traveler_when_the_env_is_unset(monkeypatch):
    monkeypatch.delenv("MERIDIAN_DEMO_TRAVELER_ID", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    sys.modules.pop("scripts.bind_current_identity", None)
    try:
        fresh = importlib.import_module("scripts.bind_current_identity")
        assert fresh.TRAVELER_ID == "trv_meridian_demo"
    finally:
        sys.modules["scripts.bind_current_identity"] = shared


def test_the_shared_binding_function_refuses_the_decoy_before_any_write(monkeypatch):
    monkeypatch.setattr(shared, "TRAVELER_ID", DECOY)
    db = Recorder()

    with pytest.raises(SystemExit, match="trv_demo_decoy.*MERIDIAN_DEMO_TRAVELER_ID"):
        shared.bind(db, provider="aws_iam", subject_id="AROAFAKE", principal=ROLE_ARN)

    assert db.calls == []


def test_the_shared_binding_function_still_binds_the_demo_traveler(monkeypatch):
    monkeypatch.setattr(shared, "TRAVELER_ID", "trv_meridian_demo")
    db = Recorder()

    shared.bind(db, provider="aws_iam", subject_id="AROAFAKE", principal=ROLE_ARN)

    assert db.calls == ["execute_statement"]


@pytest.fixture
def fake_aws(monkeypatch, tmp_path):
    db = Recorder()
    monkeypatch.setattr("boto3.client", lambda *_a, **_k: db)
    monkeypatch.setattr(shared, "TRAVELER_ID", DECOY)
    monkeypatch.setattr(sys, "argv", ["script"])
    from scripts import bind_workflow_runtime as workflow

    state = tmp_path / "state.json"
    state.write_text(json.dumps({"targets": {"default": {"resources": {"runtimes": {
        "MeridianWorkflow": {"roleArn": ROLE_ARN}}}}}}))
    monkeypatch.setattr(workflow, "DEPLOYED_STATE", state)
    return db


@pytest.mark.parametrize("module", [
    "bind_current_identity", "bind_gateway_workload", "bind_web_backend_role",
    "bind_workflow_runtime",
])
def test_each_entry_point_refuses_the_decoy_and_writes_nothing(fake_aws, module):
    script = importlib.import_module(f"scripts.{module}")
    if module == "bind_current_identity":
        script.CLUSTER_ARN = script.SECRET_ARN = "arn:aws:rds:us-east-1:123456789012:cluster:x"

    with pytest.raises(SystemExit, match="trv_demo_decoy"):
        script.main()

    assert fake_aws.calls == []
