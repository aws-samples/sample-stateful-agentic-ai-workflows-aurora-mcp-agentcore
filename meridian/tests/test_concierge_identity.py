"""The Concierge Runtime reads the caller from the forwarded token in jwt mode and only then."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from backend.agentcore import caller_claims
from backend.agentcore.errors import CallerTokenExpired
from tests.jwt_support import access_token
from tests.test_runtime_span_timings import SDK_MODULES, FakeApp, FakeClock, FakeSession

RUNTIME = Path(__file__).resolve().parents[1] / "meridian_agentcore" / "app" / "MeridianConcierge"
sys.path.insert(0, str(RUNTIME))

import caller_identity  # noqa: E402
import gateway_auth  # noqa: E402

JORDAN, DECOY = "trv_meridian_demo", "trv_demo_decoy"
NOW = 1_800_000_000.0


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


TOKENS = {
    "good": access_token(JORDAN),
    "expired": access_token(JORDAN, expires_in=-1),
    "nearly expired": access_token(JORDAN, expires_in=5),
    "id token": access_token(JORDAN, token_use="id"),
    "no traveler": access_token(None),
    "bad traveler": access_token("trv bad;x"),
    "not a jwt": "abc",
    "two dots, bad base64": "a.!!.c",
}


def outcome(function, token, now=lambda: NOW):
    try:
        return function(token, now=now)
    except CallerTokenExpired:
        return "token_expired"
    except caller_claims.CallerClaimsError:
        return "authorization"
    except caller_identity.CallerRefused as refused:
        return refused.code


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def good_claims(**overrides):
    return {"sub": "sub-j", "token_use": "access", "client_id": "client-web",
            "traveler_id": JORDAN, "exp": NOW + 3600, **overrides}


def claims_token(claims):
    return f"{b64(b'{}')}.{b64(json.dumps(claims).encode())}.sig"


def raw_token(payload: bytes) -> str:
    return f"{b64(b'{}')}.{b64(payload)}.sig"


def hostile_tokens():
    header, payload = b64(b'{"alg": "RS256"}'), b64(json.dumps(good_claims()).encode())
    std = base64.b64encode(b'{"x": "??>>~~"}').decode()
    utf16 = b64(json.dumps(good_claims()).encode("utf-16"))
    return {
        "junk": f"{header}.{payload[:4]}!!{payload[4:]}.sig",
        "newline": f"{header}.{payload[:4]}\n{payload[4:]}.sig",
        "space_inside": f"{header}.{payload[:4]} {payload[4:]}.sig",
        "std_alphabet": f"{header}.{std}.sig",
        "equals_padding": f"{header}.{payload}==.sig",
        "nul_in_header": f"{header[:3]}\x00{header[3:]}.{payload}.sig",
        "control_in_signature": f"{header}.{payload}.si\x01g",
        "leading_space": f" {header}.{payload}.sig",
        "trailing_newline": f"{header}.{payload}.sig\n",
        "empty_header": f".{payload}.sig",
        "empty_payload": f"{header}..sig",
        "utf16_payload": f"{header}.{utf16}.sig",
        "empty_signature_accepted": f"{header}.{payload}.",
        "non_ascii": "\u00e9.\u00e9.\u00e9",
        "lone_surrogate": "a.\ud800.c",
        "four_parts": "a.b.c.d",
        "empty": "",
        "oversized": "a." + "A" * 9000 + ".c",
        "list_payload": claims_token([1, 2]),
        "not_json": "a." + b64(b"not json") + ".c",
        "nan_exp": raw_token(b'{"exp": NaN, "token_use": "access", "traveler_id": "t"}'),
        "infinity_exp": raw_token(b'{"exp": Infinity, "token_use": "access", "traveler_id": "t"}'),
        "huge_exp": raw_token(b'{"exp": 1' + b"0" * 400 + b', "token_use": "access"}'),
        "bool_exp": claims_token(good_claims(exp=True)),
        "string_exp": claims_token(good_claims(exp="9999999999")),
        "deep_nesting": raw_token(b"[" * 5000),
        "invalid_utf8": raw_token(b"\xff\xfe\x00"),
        "lone_surrogate_json": raw_token(b'{"a": "\\ud800"'),
        "empty_json": raw_token(b""),
        "newline_traveler": claims_token(good_claims(traveler_id="trv_a\n")),
        "long_traveler": claims_token(good_claims(traveler_id="t" * 51)),
        "max_traveler": claims_token(good_claims(traveler_id="t" * 50)),
        "no_token_use": claims_token({k: v for k, v in good_claims().items() if k != "token_use"}),
        "boundary_exp": claims_token(good_claims(exp=NOW + 10)),
        "past_boundary_exp": claims_token(good_claims(exp=NOW + 11)),
        "non_string": 7,
        "none": None,
        "bytes": b"a.b.c",
    }


@pytest.mark.parametrize("name", list(TOKENS))
def test_the_concierge_copy_agrees_with_the_backend_module_on_every_token(name):
    token = TOKENS[name]
    assert outcome(caller_identity.traveler_from_token, token, None) == outcome(
        caller_claims.traveler_from_token, token, None)


@pytest.mark.parametrize("name", sorted(hostile_tokens()))
def test_the_concierge_copy_agrees_with_the_backend_module_on_hostile_tokens(name):
    token = hostile_tokens()[name]
    assert outcome(caller_identity.traveler_from_token, token) == outcome(
        caller_claims.traveler_from_token, token)


@pytest.mark.parametrize("name", [n for n in sorted(hostile_tokens()) if not n.endswith(
    ("accepted", "max_traveler", "past_boundary_exp"))])
def test_every_hostile_token_is_refused_by_the_concierge_copy(name):
    assert outcome(caller_identity.traveler_from_token, hostile_tokens()[name]) in (
        "authorization", "token_expired")


@pytest.mark.parametrize("name", ["empty_signature_accepted", "max_traveler",
                                  "past_boundary_exp"])
def test_the_concierge_copy_accepts_what_the_backend_accepts(name):
    token = hostile_tokens()[name]
    assert caller_identity.traveler_from_token(token, now=lambda: NOW) in (JORDAN, "t" * 50)


def test_a_refusal_never_contains_the_token():
    token = claims_token(good_claims(token_use="id")).replace(".sig", ".SECRETSIGNATURE")
    with pytest.raises(caller_identity.CallerRefused) as refused:
        caller_identity.traveler_from_token(token, now=lambda: NOW)
    assert "SECRETSIGNATURE" not in str(refused.value)
    assert token.split(".")[1] not in str(refused.value)


def test_the_two_copies_share_their_constants():
    assert caller_identity.TRAVELER_ID_PATTERN.pattern == caller_claims.TRAVELER_ID_PATTERN.pattern
    assert caller_identity.EXPIRY_MARGIN_SECONDS == caller_claims.EXPIRY_MARGIN_SECONDS
    assert caller_identity.MAX_TOKEN_CHARS == caller_claims.MAX_TOKEN_CHARS


def test_iam_mode_trusts_the_payload_and_forwards_nothing():
    assert caller_identity.caller_from_request({"traveler_id": DECOY}, bearer(TOKENS["good"]),
                                               "iam") == (None, None)


def test_jwt_mode_returns_the_claim_and_the_token():
    payload = {"traveler_id": JORDAN}
    assert caller_identity.caller_from_request(payload, bearer(TOKENS["good"]), "jwt") == (
        JORDAN, TOKENS["good"])


def test_jwt_mode_refuses_a_missing_token_and_a_different_traveler():
    with pytest.raises(caller_identity.CallerRefused) as none:
        caller_identity.caller_from_request({}, None, "jwt")
    assert none.value.code == "authorization"
    with pytest.raises(caller_identity.CallerRefused) as other:
        caller_identity.caller_from_request({"traveler_id": DECOY}, bearer(TOKENS["good"]), "jwt")
    assert "different traveler" in str(other.value)


def test_the_gateway_arguments_follow_the_mode():
    session = SimpleNamespace(get_credentials=lambda: None)
    assert set(gateway_auth.gateway_client_arguments("iam", session, "us-east-1", None)) == {
        "auth_provider"}
    assert gateway_auth.gateway_client_arguments("jwt", None, "us-east-1", "tok") == {
        "headers": {"Authorization": "Bearer tok"}}
    with pytest.raises(ValueError, match="needs the caller's access token"):
        gateway_auth.gateway_client_arguments("jwt", None, "us-east-1", None)


def test_the_mode_reads_the_environment_and_refuses_typos(monkeypatch):
    monkeypatch.delenv("MERIDIAN_AGENTCORE_AUTH", raising=False)
    assert caller_identity.auth_mode() == "iam"
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "JWT")
    assert caller_identity.auth_mode() == "jwt"
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "oauth")
    with pytest.raises(ValueError, match="MERIDIAN_AGENTCORE_AUTH"):
        caller_identity.auth_mode()


@pytest.fixture
def runtime(monkeypatch):
    clock = FakeClock()
    for name in SDK_MODULES:
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    config = sys.modules["bedrock_agentcore.memory.integrations.strands.config"]
    config.AgentCoreMemoryConfig = config.RetrievalConfig = object
    manager = sys.modules["bedrock_agentcore.memory.integrations.strands.session_manager"]
    manager.AgentCoreMemorySessionManager = object
    sys.modules["bedrock_agentcore.runtime"].BedrockAgentCoreApp = FakeApp
    monkeypatch.setenv("AGENTCORE_GATEWAY_MERIDIAN_AURORA_URL", "https://gw-1.gateway.example/mcp")
    monkeypatch.setenv("MEMORY_MERIDIAN_SESSION_ID", "mem-1")
    spec = importlib.util.spec_from_file_location("meridian_runtime_identity", RUNTIME / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.SESSION = FakeSession(clock)
    module.created = []
    module.memory_calls = []

    class Gateway:
        def __init__(self, **kwargs):
            module.created.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def list_tools_sync(self):
            return [SimpleNamespace(tool_name="MeridianHolds___get_package_details")]

    module.MCPClient = Gateway
    original = module.memory_span

    def memory_span(traveler_id, conversation_id):
        module.memory_calls.append(traveler_id)
        return original(traveler_id, conversation_id)

    module.memory_span = memory_span
    return module


async def first_events(events, count):
    taken = []
    async for event in events:
        taken.append(event)
        if len(taken) == count:
            break
    await events.aclose()
    return taken


PAYLOAD = {"event": "concierge_turn", "traveler_id": JORDAN, "conversation_id": "conv-1"}


def test_iam_mode_signs_requests_and_labels_them(runtime):
    started, tools, _ = asyncio.run(first_events(runtime.run(PAYLOAD), 3))
    assert set(runtime.created[0]) == {"url", "auth_provider"}
    assert "IAM-signed" in tools["details"]
    assert runtime.memory_calls == [JORDAN]


def test_jwt_mode_forwards_the_callers_token_and_uses_the_claims_traveler(runtime, monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    payload = {k: v for k, v in PAYLOAD.items() if k != "traveler_id"}
    _, tools, _ = asyncio.run(first_events(runtime.run(payload, bearer(TOKENS["good"])), 3))
    assert runtime.created[0] == {
        "url": "https://gw-1.gateway.example/mcp",
        "headers": {"Authorization": f"Bearer {TOKENS['good']}"}}
    assert "bearer-token" in tools["details"]
    assert runtime.memory_calls == [JORDAN]


async def collect_invoke(runtime, payload, headers):
    context = SimpleNamespace(request_headers=headers)
    return [json.loads(item) async for item in runtime.invoke(payload, context)]


@pytest.mark.parametrize(("headers", "code"), [
    (None, "authorization"),
    (bearer(TOKENS["id token"]), "authorization"),
    (bearer(TOKENS["expired"]), "token_expired"),
])
def test_jwt_mode_refuses_with_a_coded_error_and_never_calls_the_gateway(
    runtime, monkeypatch, headers, code
):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    events = asyncio.run(collect_invoke(runtime, PAYLOAD, headers))
    assert [e["type"] for e in events] == ["error"] and events[0]["code"] == code
    assert runtime.created == []
    assert TOKENS["good"] not in json.dumps(events)


def test_a_payload_naming_another_traveler_is_refused_before_anything_runs(runtime, monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    payload = {**PAYLOAD, "traveler_id": DECOY}
    events = asyncio.run(collect_invoke(runtime, payload, bearer(TOKENS["good"])))
    assert events[0]["code"] == "authorization" and runtime.created == []


def test_the_trace_hooks_label_the_gateway_auth_from_the_turn(runtime):
    turn, _, _ = runtime.turn_context(PAYLOAD, DECOY, "Cognito access token")
    assert (turn.traveler_id, turn.gateway_auth) == (DECOY, "Cognito access token")
    default, _, _ = runtime.turn_context(PAYLOAD)
    assert (default.traveler_id, default.gateway_auth) == (JORDAN, "SigV4")


def test_jwt_mode_never_puts_the_token_in_an_event_and_pins_the_claims_traveler(
    runtime, monkeypatch
):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    payload = {k: v for k, v in PAYLOAD.items() if k != "traveler_id"}
    events = asyncio.run(first_events(runtime.run(payload, bearer(TOKENS["good"])), 3))
    assert TOKENS["good"] not in json.dumps(events)
    assert TOKENS["good"].split(".")[1] not in json.dumps(events)
    turn, _, _ = runtime.turn_context(payload, JORDAN, "Cognito access token")
    assert turn.traveler_id == JORDAN


def test_the_tools_span_says_which_authentication_the_gateway_call_used(runtime):
    assert "bearer-token" in runtime.tools_span([], 1, "jwt")["details"]
    assert "IAM-signed" in runtime.tools_span([], 1)["details"]
    assert runtime.GATEWAY_AUTH_LABELS == {"iam": "SigV4", "jwt": "Cognito access token"}
