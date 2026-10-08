"""Release and proof scripts refuse abbreviated flags, and publish.py needs its confirmation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import (
    kill_and_resume_proof,
    lost_response_proof,
    publish,
    render_agentcore_config,
    stop_and_resume_proof,
    validate_demo,
    warm_demo,
)
from scripts.identity_release import settings

PARSERS = [
    (warm_demo.build_parser, ["--host"]),
    (validate_demo.build_parser, ["--allow"]),
    (kill_and_resume_proof.build_parser, ["--worker-l"]),
    (lost_response_proof.build_parser, ["--worker-l"]),
    (stop_and_resume_proof.build_parser, ["--dur", "waiting"]),
]
REQUIRED = ["--account", "123456789012", "--service-arn", "arn:x"]


@pytest.mark.parametrize("build,argv", PARSERS)
def test_an_abbreviated_flag_is_an_error_not_a_match(build, argv, capsys):
    with pytest.raises(SystemExit) as stopped:
        build().parse_args(argv)

    assert stopped.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


@pytest.mark.parametrize("build,argv", PARSERS)
def test_the_full_flag_still_parses(build, argv):
    full = {"--host": "--hosted", "--allow": "--allow-hosted-demo-writes",
            "--worker-l": "--worker-login", "--dur": "--during"}[argv[0]]

    assert build().parse_args([full, *argv[1:]]) is not None


def test_the_render_refuses_an_abbreviation():
    with pytest.raises(SystemExit) as stopped:
        render_agentcore_config.main(["--tigh"])

    assert stopped.value.code == 2


@pytest.mark.parametrize("flag", ["--app", "--st", "--tigh"])
def test_publish_refuses_an_abbreviation_with_exit_three(flag, capsys):
    with pytest.raises(SystemExit) as stopped:
        publish.build_parser().parse_args([*REQUIRED, flag])

    assert stopped.value.code == 3
    assert "unrecognized arguments" in capsys.readouterr().err


def test_publish_masks_an_account_id_in_a_usage_error(capsys):
    with pytest.raises(SystemExit):
        publish.build_parser().parse_args(["--account", "123456789012", "--bogus-123456789012"])

    assert "123456789012" not in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--apply", "--stage"])
def test_apply_and_stage_need_the_confirmation_flag(flag, monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(publish, "publish", lambda args: ran.append(args))
    monkeypatch.setattr(publish.sys, "argv", ["publish.py", *REQUIRED, flag])

    with pytest.raises(SystemExit) as stopped:
        publish.main()

    assert stopped.value.code == 3 and ran == []
    assert settings.CONFIRM_FLAG in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--apply", "--stage"])
def test_apply_and_stage_run_with_the_confirmation_flag(flag, monkeypatch):
    ran = []
    monkeypatch.setattr(publish, "publish", lambda args: ran.append(args))
    monkeypatch.setattr(publish.sys, "argv",
                        ["publish.py", *REQUIRED, flag, settings.CONFIRM_FLAG])

    assert publish.main() == 0 and len(ran) == 1


def test_a_plan_and_a_tighten_plan_need_no_confirmation(monkeypatch):
    ran = []
    monkeypatch.setattr(publish, "publish", lambda args: ran.append(args))
    monkeypatch.setattr(publish.sys, "argv", ["publish.py", *REQUIRED, "--tighten"])

    assert publish.main() == 0 and len(ran) == 1


def test_require_confirmation_ignores_a_plain_plan():
    parser = publish.build_parser()
    args = SimpleNamespace(apply=False, stage=False, confirmed=False)

    assert publish.require_confirmation(parser, args) is None
