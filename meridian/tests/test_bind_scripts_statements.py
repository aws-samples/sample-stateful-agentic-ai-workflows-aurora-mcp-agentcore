"""The bind and RLS scripts write only with --apply, and the laptop binder replays no schema DDL."""

from __future__ import annotations

import importlib
import re
import sys

import pytest

from scripts import bind_current_identity as identity

ROLE_ARN = "arn:aws:iam::123456789012:role/Workload"
CLUSTER = "arn:aws:rds:us-east-1:123456789012:cluster:x"
SCHEMA_DDL = re.compile(r"^\s*(CREATE|DROP|ALTER|COMMENT)\b", re.IGNORECASE)


class FakeAws:
    """Stands in for every boto3 client and records each SQL statement sent to rds-data."""

    def __init__(self):
        self.statements: list[str] = []

    def execute_statement(self, **kwargs):
        self.statements.append(kwargs["sql"])

    def get_caller_identity(self):
        return {"Arn": ROLE_ARN, "UserId": "AIDAFAKE:session"}

    def get_function_configuration(self, **_kwargs):
        return {"Role": ROLE_ARN}

    def get_role(self, **_kwargs):
        return {"Role": {"RoleId": "AROAFAKE", "Arn": ROLE_ARN}}


@pytest.fixture
def aws(monkeypatch):
    fake = FakeAws()
    monkeypatch.setattr("boto3.client", lambda *_a, **_k: fake)
    monkeypatch.setattr(identity, "CLUSTER_ARN", CLUSTER)
    monkeypatch.setattr(identity, "SECRET_ARN", CLUSTER)
    monkeypatch.setattr(identity, "TRAVELER_ID", "trv_meridian_demo")
    return fake


def run_main(module: str, argv: list[str]):
    script = importlib.import_module(f"scripts.{module}")
    monkeyed = sys.argv
    sys.argv = ["script", *argv]
    try:
        return script.main()
    finally:
        sys.argv = monkeyed


def test_the_laptop_binder_runs_only_the_binding_upsert(aws):
    run_main("bind_current_identity", ["--apply"])

    assert len(aws.statements) == 1
    assert "INSERT INTO traveler_identity_bindings" in aws.statements[0]
    assert "ON CONFLICT" in aws.statements[0]
    assert not any(SCHEMA_DDL.match(sql) for sql in aws.statements)
    joined = " ".join(aws.statements).upper()
    assert "DROP VIEW" not in joined and "CREATE VIEW" not in joined


def test_the_laptop_binder_does_not_read_migration_005():
    source = importlib.import_module("scripts.bind_current_identity")
    assert not hasattr(source, "MIGRATION")
    assert "split_sql" not in vars(source)


@pytest.mark.parametrize("module", ["bind_current_identity", "bind_gateway_workload"])
def test_the_binders_write_nothing_without_apply(aws, capsys, module):
    run_main(module, [])

    assert aws.statements == []
    assert "--apply" in capsys.readouterr().out


def test_the_gateway_binder_writes_one_binding_with_apply(aws):
    run_main("bind_gateway_workload", ["--apply"])

    assert len(aws.statements) == 1
    assert "traveler_identity_bindings" in aws.statements[0]


def test_the_rls_script_writes_nothing_without_apply(aws, capsys, monkeypatch):
    script = importlib.import_module("scripts.apply_rls_force_and_decoy")
    monkeypatch.setattr(script, "CLUSTER_ARN", CLUSTER)
    monkeypatch.setattr(script, "SECRET_ARN", CLUSTER)

    assert script.main([]) == 0

    assert aws.statements == []
    assert "--apply" in capsys.readouterr().out


def test_the_rls_script_applies_with_apply(aws, monkeypatch):
    script = importlib.import_module("scripts.apply_rls_force_and_decoy")
    monkeypatch.setattr(script, "CLUSTER_ARN", CLUSTER)
    monkeypatch.setattr(script, "SECRET_ARN", CLUSTER)

    assert script.main(["--apply"]) == 0

    assert any("FORCE ROW LEVEL SECURITY" in sql for sql in aws.statements)
