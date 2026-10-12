"""Scripts call AgentCore as a seeded user in jwt mode and change nothing in iam mode."""

import sys

import pytest

from backend.agentcore.caller_credential import current_caller_token
from scripts import agentcore_caller
from scripts.agentcore_caller import caller_scope, user_for_traveler


def test_iam_mode_binds_nothing_and_mints_nothing(monkeypatch):
    monkeypatch.delenv("MERIDIAN_AGENTCORE_AUTH", raising=False)

    def mint(user):
        raise AssertionError("iam mode must not sign in")

    with caller_scope("jordan", mint=mint):
        assert current_caller_token() is None


def test_jwt_mode_binds_the_seeded_users_token_for_the_block_only(monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    minted = []

    def mint(user):
        minted.append(user)
        return f"token-for-{user}"

    with caller_scope("decoy", mint=mint):
        assert current_caller_token() == "token-for-decoy"
    assert minted == ["decoy"] and current_caller_token() is None


@pytest.mark.parametrize(("traveler", "user"), [
    ("trv_meridian_demo", "jordan"), ("trv_demo_decoy", "decoy"), ("trv_other", "jordan"),
])
def test_a_traveler_maps_to_the_user_whose_token_proves_it(traveler, user):
    assert user_for_traveler(traveler) == user


def test_the_workflow_smoke_script_pings_inside_a_caller_scope(monkeypatch, capsys):
    from scripts import smoke_workflow_runtime

    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    monkeypatch.setattr(agentcore_caller, "mint_access_token", lambda user: f"{user}-token")
    bound = []

    class Runtime:
        async def ping(self, session_id):
            bound.append(current_caller_token())
            return {"workflow_status": "ready", "worker_instance_id": "w"}

    monkeypatch.setattr(smoke_workflow_runtime, "get_workflow_runtime", lambda: Runtime())
    assert smoke_workflow_runtime.main() == 0
    assert bound == ["jordan-token"]
    assert "MeridianWorkflow ready" in capsys.readouterr().out


def test_the_gateway_smoke_script_lists_and_calls_inside_a_caller_scope(monkeypatch, capsys):
    from scripts import smoke_gateway_tools

    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    monkeypatch.setattr(agentcore_caller, "mint_access_token", lambda user: f"{user}-token")
    bound = []

    class Gateway:
        gateway_url = "https://gw.example/mcp"

        def list_tools(self):
            bound.append(current_caller_token())
            return [{"name": "MeridianHolds___get_package_details"}], {}

        def call_tool(self, name, arguments):
            bound.append(current_caller_token())
            text = '{"package": {"package_id": "CTY-002", "name": "Tokyo", "availability": 3}}'
            return {"result": {"content": [{"type": "text", "text": text}]}}

    monkeypatch.setattr(smoke_gateway_tools, "get_agentcore_gateway", lambda: Gateway())
    monkeypatch.setattr(sys, "argv", ["smoke_gateway_tools.py"])
    assert smoke_gateway_tools.main() == 0
    assert bound == ["jordan-token", "jordan-token"]


@pytest.mark.parametrize("script", [
    "smoke_workflow_runtime.py", "warm_demo.py", "validate_demo.py",
])
def test_a_script_that_signs_in_reads_the_mode_from_dotenv(script):
    """Without load_dotenv the script sees no jwt mode, signs with IAM and a jwt Runtime refuses it."""
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "scripts" / script).read_text()
    called = {
        node.func.id for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "load_dotenv" in called
