"""bind_workflow_runtime binds the MeridianWorkflow execution role, and only that role."""

import json
from pathlib import Path

import pytest

from scripts import bind_workflow_runtime as bind_script

ROLE_ARN = "arn:aws:iam::123456789012:role/AgentCore-meridianv2-defa-ApplicationAgentMeridianW-x"


class FakeIam:
    def __init__(self):
        self.asked: list[str] = []

    def get_role(self, RoleName: str) -> dict:
        self.asked.append(RoleName)
        return {"Role": {"RoleId": "AROAWORKFLOWROLE", "Arn": ROLE_ARN}}


def write_state(path: Path, runtimes: dict) -> Path:
    path.write_text(json.dumps({"targets": {"default": {"resources": {"runtimes": runtimes}}}}))
    return path


def test_the_workflow_role_arn_is_read_by_runtime_name(tmp_path):
    state = write_state(tmp_path / "state.json", {
        "MeridianConcierge": {"roleArn": "arn:aws:iam::1:role/concierge"},
        "MeridianWorkflow": {"roleArn": ROLE_ARN},
    })
    assert bind_script.workflow_role_arn(state) == ROLE_ARN


@pytest.mark.parametrize("runtimes", [
    {"MeridianConcierge": {"roleArn": "arn:aws:iam::1:role/concierge"}},
    {"MeridianWorkflow": {"runtimeArn": "arn:aws:bedrock-agentcore:us-east-1:1:runtime/wf"}},
    {},
])
def test_a_missing_workflow_role_says_to_deploy_first(tmp_path, runtimes):
    state = write_state(tmp_path / "state.json", runtimes)
    with pytest.raises(SystemExit) as raised:
        bind_script.workflow_role_arn(state)
    assert "deploy MeridianWorkflow first" in str(raised.value)


def test_a_missing_state_file_says_to_deploy_first(tmp_path):
    with pytest.raises(SystemExit, match="deploy MeridianWorkflow first"):
        bind_script.workflow_role_arn(tmp_path / "absent.json")


def test_run_binds_the_role_id_to_the_chosen_traveler(tmp_path, capsys):
    state = write_state(tmp_path / "state.json", {"MeridianWorkflow": {"roleArn": ROLE_ARN}})
    bound = []
    iam = FakeIam()

    code = bind_script.run(
        state, traveler_id="trv_demo_decoy", apply=True, iam=iam, db="db",
        bind=lambda db, **kwargs: bound.append((db, kwargs)))

    assert code == 0
    assert iam.asked == [ROLE_ARN.rsplit("/", 1)[-1]]
    assert bound == [("db", {"traveler_id": "trv_demo_decoy", "allow_decoy": True,
                             "provider": "aws_iam", "subject_id": "AROAWORKFLOWROLE",
                             "principal": ROLE_ARN})]
    out = capsys.readouterr().out
    assert "Bound the" in out and "trv_demo_decoy" in out
    assert "AROAWORKFLOWROLE" not in out and "123456789012" not in out


def test_run_without_apply_binds_nothing(tmp_path, capsys):
    state = write_state(tmp_path / "state.json", {"MeridianWorkflow": {"roleArn": ROLE_ARN}})
    bound = []

    bind_script.run(state, traveler_id="trv_meridian_demo", apply=False, iam=FakeIam(),
                    db="db", bind=lambda db, **kwargs: bound.append(kwargs))

    assert bound == []
    assert "Dry run" in capsys.readouterr().out
