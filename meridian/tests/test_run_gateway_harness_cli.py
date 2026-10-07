"""The command line defaults to the dry run and only an explicit pair of flags reaches AWS."""

from pathlib import Path

import pytest

from scripts import run_gateway_harness as cli

CONFIRM = "--i-understand-this-creates-aws-resources"


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(cli.runner, "dry_run", lambda env, template: seen.append("dry") or 0)
    monkeypatch.setattr(cli.runner, "run_live",
                        lambda env, template, out, deps: seen.append("live") or 0)
    monkeypatch.setattr(cli.runner, "teardown_from_ledger",
                        lambda env, template, ledger, deps: seen.append(f"teardown {ledger}") or 0)
    return seen


def test_no_flag_is_the_dry_run(calls):
    assert cli.main([]) == 0
    assert calls == ["dry"]


def test_apply_alone_is_refused_and_reaches_nothing(calls, capsys):
    assert cli.main(["--apply"]) == cli.runner.EXIT_REFUSED
    assert calls == []
    assert CONFIRM in capsys.readouterr().out


def test_apply_with_the_confirmation_is_the_live_run(calls):
    assert cli.main(["--apply", CONFIRM]) == 0
    assert calls == ["live"]


def test_teardown_alone_is_refused_and_reaches_nothing(calls, capsys):
    assert cli.main(["--teardown", "x/ledger.json"]) == cli.runner.EXIT_REFUSED
    assert calls == [] and CONFIRM in capsys.readouterr().out


def test_teardown_with_the_confirmation_takes_a_ledger_path(calls):
    path = ".local/gateway-harness/x/ledger.json"
    assert cli.main(["--teardown", path, CONFIRM]) == 0
    assert calls == [f"teardown {Path(path)}"]


def test_the_confirmation_without_a_live_flag_is_a_usage_error(calls):
    with pytest.raises(SystemExit) as refused:
        cli.main([CONFIRM])
    assert refused.value.code == 2 and calls == []


def test_apply_and_teardown_cannot_be_combined(calls):
    with pytest.raises(SystemExit) as refused:
        cli.main(["--apply", "--teardown", "ledger.json", CONFIRM])
    assert refused.value.code == 2 and calls == []


def test_the_help_documents_the_tag_permissions_and_the_exit_codes(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    text = capsys.readouterr().out + (cli.__doc__ or "")
    for action in cli.runner.TAG_PERMISSIONS:
        assert action in text
    assert CONFIRM in text and "Exit codes" in text


def test_the_output_folder_is_ignored_by_git():
    assert cli.OUTPUT.relative_to(cli.MERIDIAN_DIR).as_posix() == ".local/gateway-harness"
    ignored = (cli.MERIDIAN_DIR / ".gitignore").read_text().splitlines()
    assert ".local/" in ignored


def test_the_minter_uses_the_cognito_tokens_script_with_the_deployments_settings(monkeypatch):
    seen = {}

    class Session:
        def __init__(self, region_name=None):
            seen["region"] = region_name

        def client(self, name):
            return f"client:{name}"

    def fake_mint(user_key, **kwargs):
        seen.update(user=user_key, **kwargs)
        return "access-token"

    monkeypatch.setattr(cli, "mint_access_token", fake_mint)
    env = {"MERIDIAN_COGNITO_REGION": "us-east-1",
           "MERIDIAN_COGNITO_USER_POOL_ID": "pool", "MERIDIAN_COGNITO_APP_CLIENT_ID": "web"}
    assert cli.make_minter(env, session_factory=Session)("decoy") == "access-token"
    assert seen == {"region": "us-east-1", "user": "decoy", "sm": "client:secretsmanager",
                    "idp": "client:cognito-idp", "pool_id": "pool", "client_id": "web"}


def test_the_live_path_builds_dependencies_without_creating_a_client(calls, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("no boto3 client may be built before the runner's guard")

    monkeypatch.setattr(cli.boto3, "client", forbidden)
    monkeypatch.setattr(cli.boto3, "Session", forbidden)
    assert cli.main(["--apply", CONFIRM]) == 0
