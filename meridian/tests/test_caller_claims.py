"""A Runtime reads the traveler from the forwarded token and refuses forged shapes."""

import base64
import json

import pytest

from backend import cognito_auth
from backend.agentcore import caller_claims
from backend.agentcore.caller_claims import (
    CallerClaimsError,
    ensure_unexpired,
    traveler_from_token,
)
from backend.agentcore.errors import CallerTokenExpired

NOW = 1_800_000_000.0


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def token_with(claims, signature="sig"):
    header = b64(json.dumps({"alg": "RS256", "kid": "k1"}).encode())
    return f"{header}.{b64(json.dumps(claims).encode())}.{signature}"


def good(**overrides):
    return {"sub": "sub-j", "token_use": "access", "client_id": "client-web",
            "traveler_id": "trv_meridian_demo", "exp": NOW + 3600, **overrides}


def test_the_traveler_is_the_claim():
    assert traveler_from_token(token_with(good()), now=lambda: NOW) == "trv_meridian_demo"


def test_the_pattern_matches_the_one_the_backend_verifier_enforces():
    assert caller_claims.TRAVELER_ID_PATTERN.pattern == cognito_auth.TRAVELER_ID_PATTERN.pattern
    assert caller_claims.TRAVELER_CLAIM == cognito_auth.TRAVELER_CLAIM


@pytest.mark.parametrize("exp", [NOW - 1, NOW, NOW + 9])
def test_an_expired_or_nearly_expired_token_is_a_coded_expiry(exp):
    with pytest.raises(CallerTokenExpired):
        traveler_from_token(token_with(good(exp=exp)), now=lambda: NOW)
    with pytest.raises(CallerTokenExpired):
        ensure_unexpired(token_with(good(exp=exp)), now=lambda: NOW)


def test_a_token_with_more_than_the_margin_left_is_accepted():
    ensure_unexpired(token_with(good(exp=NOW + 11)), now=lambda: NOW)


@pytest.mark.parametrize(("claims", "reason"), [
    (good(token_use="id"), "token_use"),
    ({k: v for k, v in good().items() if k != "token_use"}, "token_use"),
    ({k: v for k, v in good().items() if k != "traveler_id"}, "traveler_claim"),
    (good(traveler_id=""), "traveler_claim"),
    (good(traveler_id=7), "traveler_claim"),
    (good(traveler_id="trv bad;drop"), "traveler_claim"),
    (good(traveler_id="t" * 51), "traveler_claim"),
    ({k: v for k, v in good().items() if k != "exp"}, "missing_claim"),
    (good(exp=True), "missing_claim"),
    (good(exp="soon"), "missing_claim"),
])
def test_a_token_a_verified_one_cannot_be_is_refused_with_a_reason(claims, reason):
    with pytest.raises(CallerClaimsError) as refused:
        traveler_from_token(token_with(claims), now=lambda: NOW)
    assert refused.value.reason == reason


@pytest.mark.parametrize("token", [
    "", "abc", "a.b", "a.b.c.d", "a.!!!.c", token_with([1, 2]), "é.é.é",
    "a." + b64(b"not json") + ".c", "a." + "A" * 9000 + ".c",
])
def test_garbage_is_malformed(token):
    with pytest.raises(CallerClaimsError) as refused:
        traveler_from_token(token, now=lambda: NOW)
    assert refused.value.reason == "malformed"


def test_a_refusal_never_contains_the_token():
    token = token_with(good(token_use="id"), signature="SECRETSIGNATURE")
    with pytest.raises(CallerClaimsError) as refused:
        traveler_from_token(token, now=lambda: NOW)
    assert "SECRETSIGNATURE" not in str(refused.value)
    assert token.split(".")[1] not in str(refused.value)


def raw_token(payload: bytes) -> str:
    return f"{b64(b'{}')}.{b64(payload)}.sig"


@pytest.mark.parametrize("payload", [
    b'{"exp": NaN, "token_use": "access", "traveler_id": "t"}',
    b'{"exp": Infinity, "token_use": "access", "traveler_id": "t"}',
    b'{"exp": -Infinity, "token_use": "access", "traveler_id": "t"}',
    b'{"exp": 1' + b"0" * 400 + b', "token_use": "access", "traveler_id": "t"}',
])
def test_a_non_finite_expiry_is_a_missing_claim(payload):
    with pytest.raises(CallerClaimsError) as refused:
        traveler_from_token(raw_token(payload), now=lambda: NOW)
    assert refused.value.reason == "missing_claim"


@pytest.mark.parametrize("payload", [
    b"[" * 5000,
    b"\xff\xfe\x00",
    b'{"a": "\\ud800"',
    b"",
])
def test_hostile_payloads_are_malformed(payload):
    with pytest.raises(CallerClaimsError) as refused:
        traveler_from_token(raw_token(payload), now=lambda: NOW)
    assert refused.value.reason == "malformed"


@pytest.mark.parametrize("token", [None, 7, b"a.b.c", ["a", "b", "c"]])
def test_a_non_string_token_is_malformed(token):
    with pytest.raises(CallerClaimsError) as refused:
        traveler_from_token(token, now=lambda: NOW)
    assert refused.value.reason == "malformed"


def test_a_lone_surrogate_token_is_malformed():
    with pytest.raises(CallerClaimsError) as refused:
        traveler_from_token("a.\ud800.c", now=lambda: NOW)
    assert refused.value.reason == "malformed"


def test_a_traveler_with_a_trailing_newline_is_refused():
    with pytest.raises(CallerClaimsError) as refused:
        traveler_from_token(token_with(good(traveler_id="trv_a\n")), now=lambda: NOW)
    assert refused.value.reason == "traveler_claim"


def test_the_default_clock_is_the_real_one():
    assert traveler_from_token(token_with(good(exp=4_000_000_000))) == "trv_meridian_demo"
