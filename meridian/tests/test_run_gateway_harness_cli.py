"""The command line defaults to the dry run and only an explicit pair of flags reaches AWS."""

import pytest

from scripts import run_gateway_harness as cli

CONFIRM = "--i-understand-this-creates-aws-resources"
USAGE = cli.runner.EXIT_REFUSED


@pytest.fixture
def calls(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(cli, "OUTPUT", tmp_path / "gateway-harness")
    monkeypatch.setattr(cli, "dotenv_values", lambda path: {})
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


def test_teardown_with_the_confirmation_takes_a_ledger_under_the_output_folder(calls, capsys):
    path = cli.OUTPUT / "x" / "ledger.json"
    assert cli.main(["--teardown", str(path), CONFIRM]) == 0
    assert calls == [f"teardown {path.resolve()}"]
    assert str(path.resolve()) in capsys.readouterr().out


def test_a_relative_ledger_path_is_resolved_before_it_is_checked_and_printed(
        calls, capsys, monkeypatch):
    monkeypatch.chdir(cli.OUTPUT.parent)
    assert cli.main(["--teardown", "gateway-harness/x/ledger.json", CONFIRM]) == 0
    assert calls == [f"teardown {(cli.OUTPUT / 'x' / 'ledger.json').resolve()}"]


@pytest.mark.parametrize("where", ["/etc/ledger.json", "../ledger.json",
                                   "gateway-harness/../outside/ledger.json"])
def test_a_ledger_outside_the_output_folder_is_refused_with_nothing_called(
        calls, capsys, monkeypatch, where):
    monkeypatch.chdir(cli.OUTPUT.parent)
    cli.OUTPUT.mkdir()
    assert cli.main(["--teardown", where, CONFIRM]) == cli.runner.EXIT_REFUSED
    out = capsys.readouterr().out
    assert "REFUSED" in out and "gateway-harness" in out and calls == []


def test_a_symlink_that_leaves_the_output_folder_is_refused(calls, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "ledger.json").write_text("{}")
    cli.OUTPUT.mkdir()
    (cli.OUTPUT / "link").symlink_to(outside)
    path = cli.OUTPUT / "link" / "ledger.json"
    assert cli.main(["--teardown", str(path), CONFIRM]) == cli.runner.EXIT_REFUSED
    assert calls == []


def test_the_confirmation_without_a_live_flag_is_a_usage_error(calls):
    with pytest.raises(SystemExit) as refused:
        cli.main([CONFIRM])
    assert refused.value.code == USAGE and calls == []


def test_apply_and_teardown_cannot_be_combined(calls):
    with pytest.raises(SystemExit) as refused:
        cli.main(["--apply", "--teardown", "ledger.json", CONFIRM])
    assert refused.value.code == USAGE and calls == []


@pytest.mark.parametrize("argv", [
    ["--apply", "--i-understand"],
    ["--app", CONFIRM],
    ["--apply", "--i-understand-this"],
    ["--tear", "x/ledger.json", CONFIRM],
    ["--teardown=x/ledger.json", "--i-understand-this-creates"],
])
def test_an_abbreviated_flag_is_a_usage_error_and_reaches_nothing(calls, argv):
    with pytest.raises(SystemExit) as refused:
        cli.main(argv)
    assert refused.value.code == USAGE and calls == []


def test_a_usage_error_exits_with_the_refused_code_not_the_leftovers_code(calls, capsys):
    assert cli.runner.EXIT_REFUSED != cli.runner.EXIT_LEFTOVERS
    with pytest.raises(SystemExit) as refused:
        cli.main(["--no-such-flag"])
    assert refused.value.code == cli.runner.EXIT_REFUSED
    assert "usage" in capsys.readouterr().err.lower()


def test_without_the_confirmation_nothing_is_read_or_built(monkeypatch, tmp_path, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("must not run before the confirmation flag is checked")

    monkeypatch.setattr(cli, "dotenv_values", forbidden)
    monkeypatch.setattr(cli, "make_minter", forbidden)
    monkeypatch.setattr(cli.boto3, "Session", forbidden)
    monkeypatch.setattr(cli.runner, "Dependencies", forbidden)
    assert cli.main(["--apply"]) == cli.runner.EXIT_REFUSED
    assert cli.main(["--teardown", str(tmp_path / "ledger.json")]) == cli.runner.EXIT_REFUSED
    assert capsys.readouterr().out.count(CONFIRM) == 2


def test_an_unexpected_error_prints_one_masked_line_and_exits_one(calls, monkeypatch, capsys):
    def boom(env, template, out, deps):
        raise RuntimeError("acct 123456789012 Bearer abc.def.ghi " + "x" * 2000)

    monkeypatch.setattr(cli.runner, "run_live", boom)
    assert cli.main(["--apply", CONFIRM]) == cli.runner.EXIT_FAIL
    out = capsys.readouterr().out
    assert "FAILED: RuntimeError: acct <acct> Bearer <token>" in out
    assert "Traceback" not in out and "123456789012" not in out and "abc.def.ghi" not in out
    failed = next(line for line in out.splitlines() if line.startswith("FAILED:"))
    assert len(failed) <= len("FAILED: RuntimeError: ") + cli.runner.MAX_ERROR_TEXT
    assert "--teardown" in out


def test_an_unexpected_error_in_the_dry_run_is_handled_the_same_way(calls, monkeypatch, capsys):
    def boom(env, template):
        raise KeyError("acct 111122223333")

    monkeypatch.setattr(cli.runner, "dry_run", boom)
    assert cli.main([]) == cli.runner.EXIT_FAIL
    out = capsys.readouterr().out
    assert "FAILED: KeyError:" in out and "111122223333" not in out and "Traceback" not in out


def test_an_interrupt_is_reraised_after_the_cleanup_instruction(calls, monkeypatch, capsys):
    def interrupted(env, template, out, deps):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli.runner, "run_live", interrupted)
    with pytest.raises(KeyboardInterrupt):
        cli.main(["--apply", CONFIRM])
    out = capsys.readouterr().out
    assert "INTERRUPTED" in out and "--teardown" in out and "Traceback" not in out


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
