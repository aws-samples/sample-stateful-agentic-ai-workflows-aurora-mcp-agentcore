"""The scripts that call the backend or the Gateway can do so as a seeded Cognito user."""

from __future__ import annotations

import ast
import json
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import agentcore_caller, stop_and_resume_proof, verify_agentcore, warm_demo

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def minted(users):
    def mint(user):
        users.append(user)
        return f"token-for-{user}"
    return mint


# ------------------------------------------------------------------ bearer_headers


def test_iam_mode_sends_no_header_and_signs_nobody_in(monkeypatch):
    monkeypatch.delenv("MERIDIAN_AGENTCORE_AUTH", raising=False)
    users: list[str] = []

    assert agentcore_caller.bearer_headers("jordan", mint=minted(users)) == {}
    assert users == []


def test_jwt_mode_sends_the_seeded_users_access_token(monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    users: list[str] = []

    assert agentcore_caller.bearer_headers("decoy", mint=minted(users)) == {
        "Authorization": "Bearer token-for-decoy"}
    assert users == ["decoy"]


# --------------------------------------------------------------------- warm_demo


def test_a_local_warm_up_in_iam_mode_sends_no_credential(monkeypatch):
    monkeypatch.delenv("MERIDIAN_AGENTCORE_AUTH", raising=False)
    monkeypatch.delenv("MERIDIAN_HOSTED_AUTH", raising=False)

    assert warm_demo.request_headers(False, "jordan") == {}


def test_a_hosted_warm_up_in_iam_mode_uses_the_edge_credential(monkeypatch):
    monkeypatch.delenv("MERIDIAN_AGENTCORE_AUTH", raising=False)
    monkeypatch.setenv("MERIDIAN_HOSTED_AUTH", json.dumps({"username": "u", "password": "p"}))

    assert warm_demo.request_headers(True, "jordan")["Authorization"].startswith("Basic ")


def test_a_jwt_warm_up_signs_in_as_the_user_and_ignores_the_edge_credential(monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    monkeypatch.setenv("MERIDIAN_HOSTED_AUTH", json.dumps({"username": "u", "password": "p"}))
    users: list[str] = []
    monkeypatch.setattr(agentcore_caller, "mint_access_token", minted(users))

    headers = warm_demo.request_headers(True, "decoy")

    assert headers == {"Authorization": "Bearer token-for-decoy"} and users == ["decoy"]


def test_the_warm_up_takes_the_user_to_sign_in_as():
    parser = warm_demo.build_parser()

    assert parser.parse_args([]).as_user == "jordan"
    assert parser.parse_args(["--as", "decoy"]).as_user == "decoy"
    with pytest.raises(SystemExit):
        parser.parse_args(["--as", "someone"])


# ---------------------------------------------------------- stop_and_resume_proof


def test_every_call_of_the_recovery_proof_carries_the_bearer_it_was_given(monkeypatch):
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"{}"

    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: (
        seen.append(request), Response())[1])
    monkeypatch.setattr(stop_and_resume_proof, "AUTH_HEADERS",
                        {"Authorization": "Bearer token-for-jordan"})

    stop_and_resume_proof.call("/journeys")
    stop_and_resume_proof.call("/chat", {"message": "x"})

    assert [r.get_header("Authorization") for r in seen] == ["Bearer token-for-jordan"] * 2
    assert seen[1].get_header("Content-type") == "application/json"


def test_the_recovery_proof_signs_in_once_as_the_user_its_traveler_maps_to(monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    users: list[str] = []
    monkeypatch.setattr(agentcore_caller, "mint_access_token", minted(users))
    monkeypatch.setattr(stop_and_resume_proof, "AUTH_HEADERS", {})
    seen = {}

    async def fake_run(during, keep):
        seen["headers"] = dict(stop_and_resume_proof.AUTH_HEADERS)
        return 0

    monkeypatch.setattr(stop_and_resume_proof, "run", fake_run)

    assert stop_and_resume_proof.main(["--during", "waiting"]) == 0

    assert users == ["jordan"] and seen["headers"] == {"Authorization": "Bearer token-for-jordan"}


# ---------------------------------------------------------------- verify_agentcore


def test_the_gateway_tool_check_signs_in_before_it_lists_the_tools(monkeypatch):
    events = []

    class Scope:
        def __enter__(self):
            events.append("enter")

        def __exit__(self, *exc):
            events.append("exit")
            return False

    class Adapter:
        def __init__(self, **kwargs):
            pass

        def list_tools(self):
            events.append("list")
            return [{"name": "SemanticTripSearchLambda___semantic_trip_search"}], None

    monkeypatch.setattr(verify_agentcore, "caller_scope", lambda *a, **k: Scope())
    monkeypatch.setattr("backend.agentcore.gateway.AgentCoreGatewayAdapter", Adapter)

    verify_agentcore.check_gateway_tools(SimpleNamespace(gateway_url="https://g/mcp", region="r"))

    assert events == ["enter", "list", "exit"]


# --------------------------------------------------- the entry points of the others


def main_block(path: Path) -> ast.If:
    tree = ast.parse(path.read_text())
    return next(node for node in tree.body if isinstance(node, ast.If)
                and ast.unparse(node.test) == "__name__ == '__main__'")


@pytest.mark.parametrize("script", ["kill_and_resume_proof.py", "lost_response_proof.py"])
def test_the_local_proofs_run_every_entry_point_inside_a_caller_scope(script):
    block = main_block(SCRIPTS / script)

    withs = [node for node in ast.walk(block) if isinstance(node, ast.With)]
    assert len(withs) == 1
    scope = ast.unparse(withs[0].items[0].context_expr)
    assert scope == "caller_scope(user_for_traveler(TRAVELER))"
    inside = {id(node) for node in ast.walk(withs[0])}
    entries = [node for node in ast.walk(block) if isinstance(node, ast.Call)
               and ast.unparse(node.func) == "asyncio.run"]
    assert entries and all(id(node) in inside for node in entries)


def test_the_validation_script_adds_the_users_token_to_its_requests():
    source = (SCRIPTS / "validate_demo.py").read_text()

    assert 'headers.update(bearer_headers("jordan"))' in source
