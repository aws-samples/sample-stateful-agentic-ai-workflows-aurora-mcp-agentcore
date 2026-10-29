"""sync_agentcore_env writes both runtime ARNs without one clobbering the other."""

from types import SimpleNamespace

import pytest

from scripts import sync_agentcore_env as sync

CONCIERGE = "arn:aws:bedrock-agentcore:us-east-1:1:runtime/cc"
WORKFLOW = "arn:aws:bedrock-agentcore:us-east-1:1:runtime/wf"


def config(**overrides):
    values = {name: None for name in (
        "runtime_arn", "workflow_runtime_arn", "runtime_name", "gateway_url", "gateway_name",
        "gateway_search_tool", "memory_id", "memory_name")}
    return SimpleNamespace(region="us-east-1", **{**values, **overrides})


def test_both_runtime_arns_are_lines_and_empty_values_are_not():
    lines = sync.env_lines(config(runtime_arn=CONCIERGE, workflow_runtime_arn=WORKFLOW))
    assert lines == {"AGENTCORE_REGION": "us-east-1", "AGENTCORE_RUNTIME_ARN": CONCIERGE,
                     "AGENTCORE_WORKFLOW_RUNTIME_ARN": WORKFLOW}


def test_writing_the_workflow_arn_keeps_the_concierge_arn(tmp_path):
    env = tmp_path / ".env"
    env.write_text(f"OTHER=1\nAGENTCORE_RUNTIME_ARN={CONCIERGE}\n")
    sync.write_env(env, {"AGENTCORE_WORKFLOW_RUNTIME_ARN": WORKFLOW})
    assert env.read_text() == (
        f"OTHER=1\nAGENTCORE_RUNTIME_ARN={CONCIERGE}\nAGENTCORE_WORKFLOW_RUNTIME_ARN={WORKFLOW}\n")
    sync.write_env(env, {"AGENTCORE_RUNTIME_ARN": "arn:new"})
    assert "AGENTCORE_RUNTIME_ARN=arn:new\n" in env.read_text()
    assert f"AGENTCORE_WORKFLOW_RUNTIME_ARN={WORKFLOW}\n" in env.read_text()


ACCOUNT = "123456789012"


def test_printed_lines_hide_the_account_id(monkeypatch, capsys):
    arn = f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:runtime/cc"
    cfg = config(runtime_arn=arn, gateway_url=f"https://{ACCOUNT}.example/mcp")
    cfg.sources = ["state"]
    cfg.cli_project_dir = f"/work/{ACCOUNT}"
    cfg.configured_any = True
    monkeypatch.setattr(sync, "resolve_agentcore_config", lambda: cfg)
    monkeypatch.setattr(sync.resolve_agentcore_config, "cache_clear", lambda: None, raising=False)
    monkeypatch.setattr(sync.sys, "argv", ["sync_agentcore_env.py"])
    assert sync.main() == 0
    out = capsys.readouterr().out
    assert ACCOUNT not in out
    assert "Printed values are masked; --write writes the real values" in out
    assert "AGENTCORE_RUNTIME_ARN=arn:aws:bedrock-agentcore:us-east-1:<acct>:runtime/cc" in out


def test_the_gateway_token_option_is_gone(monkeypatch):
    monkeypatch.setattr(sync.sys, "argv", ["sync_agentcore_env.py", "--fetch-gateway-token"])
    with pytest.raises(SystemExit) as exit_info:
        sync.main()
    assert exit_info.value.code == 2


@pytest.mark.parametrize("stale", ["# AGENTCORE_REGION=us-west-2\n", "X_AGENTCORE_REGION=old\n"])
def test_a_commented_or_suffixed_key_gets_the_real_line_appended(tmp_path, stale):
    env = tmp_path / ".env"
    env.write_text(stale)
    sync.write_env(env, {"AGENTCORE_REGION": "us-east-1"})
    assert env.read_text() == f"{stale}AGENTCORE_REGION=us-east-1\n"


def test_an_active_line_is_replaced_in_place(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=1\nAGENTCORE_REGION=old\nB=2\n")
    sync.write_env(env, {"AGENTCORE_REGION": "us-east-1"})
    assert env.read_text() == "A=1\nAGENTCORE_REGION=us-east-1\nB=2\n"


def test_a_backslash_in_a_value_survives_a_replace(tmp_path):
    env = tmp_path / ".env"
    env.write_text("AGENTCORE_RUNTIME_NAME=old\n")
    sync.write_env(env, {"AGENTCORE_RUNTIME_NAME": r"a\1b\n"})
    assert env.read_text() == "AGENTCORE_RUNTIME_NAME=a\\1b\\n\n"


def test_the_usage_text_says_printed_values_are_masked():
    assert "Printed values are masked" in sync.__doc__
    assert "--write" in sync.__doc__
