"""sync_agentcore_env writes both runtime ARNs without one clobbering the other."""

from types import SimpleNamespace

from scripts import sync_agentcore_env as sync

CONCIERGE = "arn:aws:bedrock-agentcore:us-east-1:1:runtime/cc"
WORKFLOW = "arn:aws:bedrock-agentcore:us-east-1:1:runtime/wf"


def config(**overrides):
    values = {name: None for name in (
        "runtime_arn", "workflow_runtime_arn", "runtime_name", "gateway_url", "gateway_name",
        "gateway_search_tool", "memory_id", "memory_name", "workload_identity",
        "resource_provider")}
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
