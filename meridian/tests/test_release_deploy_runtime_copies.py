"""`release_identity.py deploy` refuses a missing or stale staged Runtime copy."""

from __future__ import annotations

import json

import pytest

from scripts import stage_concierge_runtime, stage_workflow_runtime
from scripts.identity_release import deploy_order, stages
from tests.test_release_deploy_order import FLAG, Cli, replacement, run

CONCIERGE_FIX = "scripts/stage_concierge_runtime.py"
WORKFLOW_FIX = "scripts/stage_workflow_runtime.py"


@pytest.mark.parametrize("argv", [["deploy"], ["deploy", "--apply", FLAG]])
def test_a_stale_runtime_copy_stops_deploy_before_anything_runs(argv, tmp_path, capsys):
    world = replacement()
    runner = Cli(world)

    code = run(argv, world, tmp_path, runner, stage=stages.GATEWAY,
               stale_copies=["Concierge: concierge_definition.py (missing)"])

    captured = capsys.readouterr()
    assert code == 2
    assert "concierge_definition.py (missing)" in captured.err
    assert "DRY RUN" not in captured.out
    assert runner.diffs == [] and runner.deploys == []
    assert world.built == [] and "Traceback" not in captured.err


def test_the_refusal_names_the_command_that_fixes_each_copy(tmp_path, capsys):
    world = replacement()

    run(["deploy"], world, tmp_path, Cli(world), stage=stages.GATEWAY,
        stale_copies=["Concierge: concierge_definition.py", "Workflow: backend/config.py"])

    err = capsys.readouterr().err
    assert CONCIERGE_FIX in err and WORKFLOW_FIX in err and "backend/config.py" in err


def test_current_copies_let_deploy_go_on(tmp_path, capsys):
    world = replacement()

    assert run(["deploy"], world, tmp_path, Cli(world), stage=stages.GATEWAY) == 0

    assert "DRY RUN" in capsys.readouterr().out


def test_the_check_reads_both_stagers_in_process(monkeypatch):
    monkeypatch.setattr(stage_concierge_runtime, "check", lambda: ["concierge_definition.py"])
    monkeypatch.setattr(stage_workflow_runtime, "check", lambda: ["backend/a.py", "backend/b.py"])

    found = deploy_order.runtime_copy_problems()

    assert found == ["Concierge: concierge_definition.py", "Workflow: backend/a.py",
                     "Workflow: backend/b.py"]


def test_no_stale_file_means_no_problem(monkeypatch):
    monkeypatch.setattr(stage_concierge_runtime, "check", lambda: [])
    monkeypatch.setattr(stage_workflow_runtime, "check", lambda: [])

    assert deploy_order.runtime_copy_problems() == []


def test_the_default_dependency_is_the_real_check():
    from scripts import release_identity

    deps = release_identity.Dependencies(env={}, session=lambda region: None)

    assert deps.runtime_copy_problems is deploy_order.runtime_copy_problems


def test_an_unconfirmed_apply_on_a_stale_copy_exits_two_not_three(tmp_path, capsys):
    world = replacement()
    runner = Cli(world)

    code = run(["deploy", "--apply"], world, tmp_path, runner, stage=stages.GATEWAY,
               stale_copies=["Workflow: backend/config.py"])

    captured = capsys.readouterr()
    assert code == 2 and "REFUSED" not in captured.out and FLAG not in captured.out
    assert "backend/config.py" in captured.err
    assert world.built == [] and runner.deploys == []


def test_an_unconfirmed_apply_on_current_copies_is_still_the_usage_refusal(tmp_path, capsys):
    world = replacement()

    assert run(["deploy", "--apply"], world, tmp_path, Cli(world), stage=stages.GATEWAY) == 3

    assert FLAG in capsys.readouterr().out


@pytest.mark.parametrize("error", [
    json.JSONDecodeError("Expecting value", "{", 0),
    PermissionError("manifest"),
])
def test_a_manifest_that_cannot_be_read_is_a_stale_copy_not_an_error(monkeypatch, error):
    def unreadable():
        raise error

    monkeypatch.setattr(stage_concierge_runtime, "check", lambda: [])
    monkeypatch.setattr(stage_workflow_runtime, "check", unreadable)

    found = deploy_order.runtime_copy_problems()

    assert found == [f"Workflow: the staged copy cannot be checked ({type(error).__name__})"]


@pytest.mark.parametrize("error", [json.JSONDecodeError("Expecting value", "{", 0), OSError("x")])
def test_a_corrupt_workflow_manifest_stops_deploy_with_exit_two_naming_the_stager(
        monkeypatch, tmp_path, capsys, error):
    def unreadable():
        raise error

    monkeypatch.setattr(stage_concierge_runtime, "check", lambda: [])
    monkeypatch.setattr(stage_workflow_runtime, "check", unreadable)
    world = replacement()
    runner = Cli(world)

    code = run(["deploy"], world, tmp_path, runner, stage=stages.GATEWAY,
               copy_check=deploy_order.runtime_copy_problems)

    err = capsys.readouterr().err
    assert code == 2 and WORKFLOW_FIX in err and "Workflow:" in err
    assert "unexpected" not in err and "could not write" not in err
    assert runner.diffs == [] and runner.deploys == [] and world.built == []
