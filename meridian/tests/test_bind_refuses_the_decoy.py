"""Only the backend and workflow entry points may give the decoy an aws_iam workload binding.

The decoy (Jordan Lee, ``trv_demo_decoy``) signs in and uses the app with its own data, so the
App Runner role and the MeridianWorkflow Runtime role must act for it. The Gateway holds role and
the laptop identity stay Jordan Morgan only. The shared ``bind`` refuses the decoy unless the
caller says ``allow_decoy=True``, and only the two entry points below say it.
"""

from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path

import pytest

from scripts import bind_current_identity as shared

DECOY = "trv_demo_decoy"
JORDAN = "trv_meridian_demo"
ROLE_ARN = "arn:aws:iam::123456789012:role/Workload"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
DECOY_BINDERS = ["bind_web_backend_role", "bind_workflow_runtime"]
DECOY_REFUSERS = ["bind_current_identity", "bind_gateway_workload"]


class Recorder:
    """Stands in for every boto3 client; every rds-data write is recorded."""

    def __init__(self):
        self.calls: list[str] = []
        self.travelers: list[str] = []

    def execute_statement(self, **kwargs):
        self.calls.append("execute_statement")
        for parameter in kwargs.get("parameters", []):
            if parameter["name"] == "traveler_id":
                self.travelers.append(parameter["value"]["stringValue"])

    def get_role(self, **_kwargs):
        return {"Role": {"RoleId": "AROAFAKE", "Arn": ROLE_ARN}}

    def get_function_configuration(self, **_kwargs):
        return {"Role": ROLE_ARN}

    def describe_stacks(self, **_kwargs):
        return {"Stacks": [{"Outputs": [
            {"OutputKey": "InstanceRoleArn", "OutputValue": ROLE_ARN}]}]}

    def get_caller_identity(self):
        return {"Arn": ROLE_ARN, "UserId": "AIDAFAKE:session"}


def bind_once(db, traveler_id, **extra):
    shared.bind(db, traveler_id=traveler_id, provider="aws_iam", subject_id="AROAFAKE",
                principal=ROLE_ARN, **extra)


def test_the_default_traveler_is_the_demo_traveler_when_the_env_is_unset(monkeypatch):
    monkeypatch.delenv("MERIDIAN_DEMO_TRAVELER_ID", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    sys.modules.pop("scripts.bind_current_identity", None)
    try:
        fresh = importlib.import_module("scripts.bind_current_identity")
        assert fresh.TRAVELER_ID == JORDAN
    finally:
        sys.modules["scripts.bind_current_identity"] = shared


def test_the_shared_binding_function_refuses_the_decoy_by_default_before_any_write():
    db = Recorder()

    with pytest.raises(SystemExit, match="trv_demo_decoy.*MERIDIAN_DEMO_TRAVELER_ID"):
        bind_once(db, DECOY)

    assert db.calls == []


def test_the_shared_binding_function_binds_the_decoy_only_when_the_caller_allows_it():
    db = Recorder()

    bind_once(db, DECOY, allow_decoy=True)

    assert db.calls == ["execute_statement"]
    assert db.travelers == [DECOY]


def test_the_shared_binding_function_binds_the_traveler_it_is_given_not_the_env_default():
    db = Recorder()

    bind_once(db, JORDAN)

    assert db.travelers == [JORDAN]


@pytest.fixture
def fake_aws(monkeypatch, tmp_path):
    db = Recorder()
    monkeypatch.setattr("boto3.client", lambda *_a, **_k: db)
    monkeypatch.setattr(sys, "argv", ["script"])
    from scripts import bind_workflow_runtime as workflow

    state = tmp_path / "state.json"
    state.write_text(json.dumps({"targets": {"default": {"resources": {"runtimes": {
        "MeridianWorkflow": {"roleArn": ROLE_ARN}}}}}}))
    monkeypatch.setattr(workflow, "DEPLOYED_STATE", state)
    return db


def run_entry_point(module, argv=()):
    script = importlib.import_module(f"scripts.{module}")
    if module == "bind_current_identity":
        script.CLUSTER_ARN = script.SECRET_ARN = "arn:aws:rds:us-east-1:123456789012:cluster:x"
    sys.argv = ["script", *argv]
    return script.main()


@pytest.mark.parametrize("module", DECOY_REFUSERS)
def test_the_gateway_and_laptop_entry_points_refuse_the_decoy_and_write_nothing(
    fake_aws, monkeypatch, module
):
    monkeypatch.setattr(shared, "TRAVELER_ID", DECOY)

    with pytest.raises(SystemExit, match="trv_demo_decoy"):
        run_entry_point(module)

    assert fake_aws.calls == []


@pytest.mark.parametrize("module", DECOY_REFUSERS)
def test_the_gateway_and_laptop_entry_points_still_bind_jordan(fake_aws, monkeypatch, module):
    monkeypatch.setattr(shared, "TRAVELER_ID", JORDAN)

    run_entry_point(module)

    assert fake_aws.travelers
    assert set(fake_aws.travelers) == {JORDAN}


@pytest.mark.parametrize("module", DECOY_BINDERS)
def test_the_backend_and_workflow_entry_points_bind_the_decoy(fake_aws, module):
    run_entry_point(module, ["--traveler", DECOY, "--apply"])

    assert fake_aws.travelers == [DECOY]


@pytest.mark.parametrize("module", DECOY_BINDERS)
def test_the_backend_and_workflow_entry_points_default_to_jordan_morgan(fake_aws, module):
    run_entry_point(module, ["--apply"])

    assert fake_aws.travelers == [JORDAN]


@pytest.mark.parametrize("module", DECOY_BINDERS)
@pytest.mark.parametrize("argv", [[], ["--traveler", DECOY]])
def test_without_apply_the_backend_and_workflow_entry_points_write_nothing(
    fake_aws, capsys, module, argv
):
    run_entry_point(module, argv)

    assert fake_aws.calls == []
    assert "--apply" in capsys.readouterr().out


@pytest.mark.parametrize("module", DECOY_BINDERS)
@pytest.mark.parametrize("traveler", ["trv_unknown", "trv_demo_decoy ", ""])
def test_the_traveler_option_accepts_only_the_seeded_cognito_travelers(
    fake_aws, module, traveler
):
    with pytest.raises(SystemExit) as raised:
        run_entry_point(module, ["--traveler", traveler, "--apply"])

    assert raised.value.code == 2
    assert fake_aws.calls == []


@pytest.mark.parametrize("module", DECOY_BINDERS)
def test_the_entry_points_never_print_the_subject_id_or_the_role_arn(fake_aws, capsys, module):
    run_entry_point(module, ["--traveler", DECOY, "--apply"])

    out = capsys.readouterr().out
    assert "AROAFAKE" not in out
    assert "123456789012" not in out


def test_only_the_backend_and_workflow_scripts_allow_the_decoy():
    allowing = sorted(
        path.stem for path in SCRIPTS.glob("*.py")
        if re.search(r"allow_decoy\s*=\s*True", path.read_text(encoding="utf-8")))

    assert allowing == sorted(DECOY_BINDERS)
