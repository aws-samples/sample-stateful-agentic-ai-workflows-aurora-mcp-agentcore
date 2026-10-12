"""The scripts that call the backend or the Gateway can do so as a seeded Cognito user."""

from __future__ import annotations

import ast
import asyncio
import json
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from backend.agentcore.caller_credential import current_caller_token
from scripts import (
    agentcore_caller, kill_and_resume_proof, lost_response_proof, stop_and_resume_proof,
    validate_demo, verify_agentcore, warm_demo,
)

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


def run_warm_up(monkeypatch, argv):
    """Run the warm-up's main against a fake transport; return what it sent."""
    sent = []

    def fake_call(base, headers, path, body=None):
        sent.append((base, dict(headers), path, body))
        return {}

    monkeypatch.setattr(warm_demo, "call", fake_call)
    warm_demo.main(argv)
    return sent


def test_signing_in_as_the_decoy_warms_the_decoys_own_traveler(monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    monkeypatch.setattr(agentcore_caller, "mint_access_token", minted([]))

    sent = run_warm_up(monkeypatch, ["--as", "decoy"])

    assert any(path == "/api/memory/trv_demo_decoy" for _b, _h, path, _body in sent)
    bodies = [body for _b, _h, _p, body in sent if body]
    assert bodies and {body["customer_id"] for body in bodies} == {"trv_demo_decoy"}
    assert not any("trv_meridian_demo" in str(item) for item in sent)


def test_signing_in_as_jordan_warms_the_demo_traveler(monkeypatch):
    monkeypatch.delenv("MERIDIAN_AGENTCORE_AUTH", raising=False)

    sent = run_warm_up(monkeypatch, [])

    assert any(path == "/api/memory/trv_meridian_demo" for _b, _h, path, _body in sent)


def test_the_decoys_profile_is_judged_against_the_decoy():
    check = warm_demo.steps("trv_demo_decoy")[2][3]

    assert check({"traveler_id": "trv_demo_decoy", "facts": []}) is None
    assert check({"traveler_id": "trv_meridian_demo", "facts": []}) is not None


@pytest.mark.parametrize("url", ["http://demo.example.com", "http://10.0.0.5:8013",
                                 "http://127.0.0.1.evil.example"])
def test_a_token_is_never_sent_to_a_plain_http_remote_target(monkeypatch, url):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    users: list[str] = []
    monkeypatch.setattr(agentcore_caller, "mint_access_token", minted(users))

    with pytest.raises(SystemExit):
        run_warm_up(monkeypatch, ["--base-url", url])

    assert users == []


@pytest.mark.parametrize("url", ["http://127.0.0.1:8014", "http://localhost:8013",
                                 "https://demo.example.com"])
def test_a_token_goes_to_https_or_loopback_targets(monkeypatch, url):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    monkeypatch.setattr(agentcore_caller, "mint_access_token", minted([]))

    sent = run_warm_up(monkeypatch, ["--base-url", url])

    assert sent and {headers["Authorization"] for _b, headers, _p, _x in sent} == {
        "Bearer token-for-jordan"}


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


def test_the_recovery_proof_forgets_its_token_when_it_ends(monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    monkeypatch.setattr(agentcore_caller, "mint_access_token", minted([]))

    async def fake_run(during, keep):
        return 0

    monkeypatch.setattr(stop_and_resume_proof, "run", fake_run)

    stop_and_resume_proof.main(["--during", "waiting"])

    assert stop_and_resume_proof.AUTH_HEADERS == {}


def test_the_recovery_proof_will_not_sign_in_for_a_plain_http_remote_api(monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    users: list[str] = []
    monkeypatch.setattr(agentcore_caller, "mint_access_token", minted(users))
    monkeypatch.setattr(stop_and_resume_proof, "API", "http://demo.example.com/api")

    with pytest.raises(SystemExit):
        stop_and_resume_proof.main(["--during", "waiting"])

    assert users == [] and stop_and_resume_proof.AUTH_HEADERS == {}


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


# ------------------------------------------------------------------ validate_demo


def sent_headers(auth, headers):
    """The Authorization header httpx puts on a request built with these options."""
    seen = []

    def handler(request):
        seen.append(request.headers.get("authorization"))
        return httpx.Response(200, json={})

    with httpx.Client(base_url="http://t", auth=auth, headers=headers,
                      transport=httpx.MockTransport(handler)) as client:
        client.get("/x")
    return seen[0]


def test_a_jwt_validation_run_sends_the_bearer_not_the_edge_credential(monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    monkeypatch.setattr(agentcore_caller, "mint_access_token", minted([]))
    environ = {"MERIDIAN_HOSTED_AUTH": json.dumps({"username": "u", "password": "p"}),
               "MERIDIAN_API_TOKEN": "shared-token"}

    auth, headers = validate_demo.request_auth(environ)

    assert auth is None
    assert sent_headers(auth, headers) == "Bearer token-for-jordan"


def test_an_iam_validation_run_keeps_the_edge_credential_and_shared_token(monkeypatch):
    monkeypatch.delenv("MERIDIAN_AGENTCORE_AUTH", raising=False)
    both = {"MERIDIAN_HOSTED_AUTH": json.dumps({"username": "u", "password": "p"}),
            "MERIDIAN_API_TOKEN": "shared-token"}

    auth, headers = validate_demo.request_auth(both)
    assert sent_headers(auth, headers).startswith("Basic ")

    auth, headers = validate_demo.request_auth({"MERIDIAN_API_TOKEN": "shared-token"})
    assert auth is None and sent_headers(auth, headers) == "Bearer shared-token"

    assert validate_demo.request_auth({}) == (None, {})


def test_an_unresolved_shared_token_is_not_sent(monkeypatch):
    monkeypatch.delenv("MERIDIAN_AGENTCORE_AUTH", raising=False)

    assert validate_demo.request_auth({"MERIDIAN_API_TOKEN": "{{resolve:x}}"}) == (None, {})


def test_a_malformed_edge_credential_is_refused(monkeypatch):
    monkeypatch.delenv("MERIDIAN_AGENTCORE_AUTH", raising=False)

    with pytest.raises(ValueError):
        validate_demo.request_auth({"MERIDIAN_HOSTED_AUTH": "not json"})


# ------------------------------------------------------------------- token hygiene

TOKEN = "eyJ-secret-minted-token"


def mint_secret(user):
    return TOKEN


def test_minting_a_bearer_prints_nothing(monkeypatch, capsys):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")

    headers = agentcore_caller.bearer_headers("jordan", mint=mint_secret)

    out = capsys.readouterr()
    assert headers["Authorization"].endswith(TOKEN)
    assert TOKEN not in out.out and TOKEN not in out.err


def test_a_caller_scope_binds_the_token_without_printing_or_exporting_it(monkeypatch, capsys):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    monkeypatch.setenv("AURORA_WORKFLOW_SECRET_ARN", "arn:aws:secretsmanager:r:000000000000:s:x")
    import os

    with agentcore_caller.caller_scope("jordan", mint=mint_secret):
        assert current_caller_token() == TOKEN
        assert not any(TOKEN in value for value in os.environ.values())
        assert not any(TOKEN in value for value in
                       kill_and_resume_proof.worker_env(True, os.environ).values())

    out = capsys.readouterr()
    assert TOKEN not in out.out and TOKEN not in out.err
    assert current_caller_token() is None


class SpawnRecorder:
    """Stands in for asyncio.create_subprocess_exec; records what it was given and stops."""

    def __init__(self):
        self.calls = []

    async def __call__(self, *argv, **kwargs):
        self.calls.append((argv, kwargs))
        raise RuntimeError("spawn recorded")


def fake_database(monkeypatch, module):
    @asynccontextmanager
    async def scoped(client):
        yield None

    async def async_none(*args, **kwargs):
        return "journey-1"

    for name, value in {
        "get_rds_data_client": lambda: SimpleNamespace(),
        "scoped": scoped,
        "ScopedDb": lambda client, tx: None,
        "create_journey": async_none,
        "bind_thread": async_none,
        "_purge": async_none,
        "workflow_store_status": lambda: {"kind": "fake", "durable": True},
    }.items():
        if hasattr(module, name):
            monkeypatch.setattr(module, name, value)
    if hasattr(module, "service"):
        monkeypatch.setattr(module.service, "workflow_store_status",
                            lambda: {"kind": "fake", "durable": True})


def assert_spawns_carry_no_token(recorder):
    assert recorder.calls
    for argv, kwargs in recorder.calls:
        assert not any(TOKEN in str(part) for part in argv)
        assert not any(TOKEN in str(value) for value in kwargs["env"].values())
        assert "CALLER_TOKEN" not in kwargs["env"]


@pytest.mark.parametrize("module, entry", [
    (kill_and_resume_proof, lambda m: m.main(False, True)),
    (lost_response_proof, lambda m: m.main(True)),
    (kill_and_resume_proof, lambda m: m.run_takeover("thread-1", True)),
])
def test_the_workers_the_proofs_spawn_are_given_no_token(monkeypatch, module, entry):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    monkeypatch.setenv("AURORA_WORKFLOW_SECRET_ARN", "arn:aws:secretsmanager:r:000000000000:s:x")
    recorder = SpawnRecorder()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", recorder)
    fake_database(monkeypatch, module)

    with agentcore_caller.caller_scope("jordan", mint=mint_secret):
        assert current_caller_token() == TOKEN
        with pytest.raises(RuntimeError, match="spawn recorded"):
            asyncio.run(entry(module))

    assert_spawns_carry_no_token(recorder)
