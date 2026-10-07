"""The API seam accepts a verified Cognito identity and never lets a caller choose its traveler."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend import http_auth
from backend.cognito_auth import (
    CognitoConfig,
    CognitoUnavailable,
    CognitoVerifier,
    InvalidCognitoToken,
)
from backend.http_auth import CURRENT_TRAVELER, HttpPrincipal, authorize_traveler
from backend.main import app

POOL = "us-east-1_AbCdEfGhI"
CLIENT = "client-web"
CONFIG = CognitoConfig("us-east-1", POOL, CLIENT)
JORDAN = ("sub-jordan", "trv_meridian_demo")
DECOY = ("sub-decoy", "trv_demo_decoy")


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token_for(key, who, **overrides):
    now = int(time.time())
    claims = {
        "sub": who[0], "iss": CONFIG.issuer, "client_id": CLIENT, "token_use": "access",
        "traveler_id": who[1], "iat": now, "exp": now + 3600, **overrides,
    }
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption())
    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture
def cognito(key, monkeypatch):
    verifier = CognitoVerifier(CONFIG, signing_key_for=lambda token: key.public_key())
    monkeypatch.setattr(http_auth, "get_cognito_verifier", lambda: verifier)
    return verifier


def request_from(host):
    return Request({
        "type": "http", "method": "GET", "path": "/api/me", "headers": [],
        "client": (host, 12345), "server": ("localhost", 8000), "scheme": "http",
        "query_string": b"",
    })


def authenticate(host, header):
    return asyncio.run(http_auth.require_http_principal(request_from(host), header))


def test_a_verified_token_binds_the_traveler_from_its_claim(key, cognito):
    principal = authenticate("203.0.113.8", f"Bearer {token_for(key, DECOY)}")
    assert principal == HttpPrincipal("sub-decoy", "trv_demo_decoy", "cognito")


def test_a_signed_in_decoy_cannot_name_jordan(key, cognito):
    principal = authenticate("203.0.113.8", f"Bearer {token_for(key, DECOY)}")
    with pytest.raises(HTTPException) as refused:
        authorize_traveler(principal, "trv_meridian_demo")
    assert refused.value.status_code == 403


def test_jordans_own_token_works_for_jordan(key, cognito):
    principal = authenticate("203.0.113.8", f"Bearer {token_for(key, JORDAN)}")
    assert authorize_traveler(principal, "trv_meridian_demo") == "trv_meridian_demo"


@pytest.mark.parametrize("requested", [None, "", CURRENT_TRAVELER])
def test_no_id_and_the_current_traveler_alias_both_mean_the_principal(requested):
    decoy = HttpPrincipal("sub-decoy", "trv_demo_decoy", "cognito")
    assert authorize_traveler(decoy, requested) == "trv_demo_decoy"


def test_a_token_that_fails_verification_is_a_401_with_a_challenge(key, cognito):
    expired = token_for(key, JORDAN, exp=int(time.time()) - 3600)
    with pytest.raises(HTTPException) as refused:
        authenticate("203.0.113.8", f"Bearer {expired}")
    assert refused.value.status_code == 401
    assert refused.value.headers == {"WWW-Authenticate": "Bearer"}
    assert expired not in str(refused.value.detail)


def test_a_cognito_failure_never_falls_back_to_the_shared_token(key, cognito, monkeypatch):
    monkeypatch.setenv("MERIDIAN_API_TOKEN", "shared-secret")
    wrong_client = token_for(key, JORDAN, client_id="someone-elses-client")
    with pytest.raises(HTTPException) as refused:
        authenticate("203.0.113.8", f"Bearer {wrong_client}")
    assert refused.value.status_code == 401


def test_the_shared_token_still_works_while_cognito_is_configured(cognito, monkeypatch):
    monkeypatch.setenv("MERIDIAN_API_TOKEN", "shared-secret")
    monkeypatch.setenv("MERIDIAN_API_TRAVELER_ID", "trv_meridian_demo")
    principal = authenticate("203.0.113.8", "Bearer shared-secret")
    assert principal.authentication == "bearer"
    assert principal.traveler_id == "trv_meridian_demo"


def test_a_cognito_token_works_while_the_shared_token_is_also_configured(
    key, cognito, monkeypatch
):
    monkeypatch.setenv("MERIDIAN_API_TOKEN", "shared-secret")
    principal = authenticate("203.0.113.8", f"Bearer {token_for(key, DECOY)}")
    assert principal.authentication == "cognito" and principal.traveler_id == "trv_demo_decoy"


def test_unreachable_signing_keys_are_a_503_not_a_login_failure(key, monkeypatch):
    class Down:
        def verify(self, token):
            raise CognitoUnavailable("keys down")

    monkeypatch.setattr(http_auth, "get_cognito_verifier", lambda: Down())
    with pytest.raises(HTTPException) as refused:
        authenticate("203.0.113.8", f"Bearer {token_for(key, JORDAN)}")
    assert refused.value.status_code == 503


def test_a_remote_caller_without_a_token_is_asked_to_sign_in(cognito, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(HTTPException) as refused:
        authenticate("203.0.113.8", None)
    assert refused.value.status_code == 401


def test_loopback_development_still_runs_without_a_token(cognito, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("MERIDIAN_API_TOKEN", raising=False)
    monkeypatch.delenv("MERIDIAN_ALLOW_INSECURE_LOCALHOST", raising=False)
    principal = authenticate("127.0.0.1", None)
    assert principal.authentication == "loopback-development"


def test_a_bad_token_is_refused_even_on_loopback(cognito, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    with pytest.raises(HTTPException) as refused:
        authenticate("127.0.0.1", "Bearer not-a-token")
    assert refused.value.status_code == 401


def test_me_reports_the_verified_traveler(key, cognito):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token_for(key, DECOY)}"}
    response = client.get("/api/me", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"traveler_id": "trv_demo_decoy", "authentication": "cognito"}


def test_me_ignores_a_traveler_the_caller_supplies(key, cognito):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token_for(key, DECOY)}",
               "X-Traveler-Id": "trv_meridian_demo"}
    body = client.get("/api/me?traveler_id=trv_meridian_demo", headers=headers).json()
    assert body["traveler_id"] == "trv_demo_decoy"


def test_a_signed_in_decoy_is_refused_jordans_memory_before_any_database_read(key, cognito):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token_for(key, DECOY)}"}
    response = client.get("/api/memory/trv_meridian_demo", headers=headers)
    assert response.status_code == 403
    assert response.json() == {
        "error": "The authenticated caller is not authorized for that traveler."}


def test_the_chat_route_refuses_jordans_id_from_the_decoy(key, cognito):
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token_for(key, DECOY)}"}
    response = client.post(
        "/api/chat", headers=headers,
        json={"message": "Show me city trips.", "phase": 1, "customer_id": "trv_meridian_demo"})
    assert response.status_code == 403


def test_an_unsigned_request_to_me_is_refused_when_cognito_is_the_only_credential(
    cognito, monkeypatch
):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("MERIDIAN_API_TOKEN", raising=False)
    response = TestClient(app, client=("203.0.113.8", 4000)).get("/api/me")
    assert response.status_code == 401


REMOTE = ("203.0.113.8", 4000)
CHALLENGE = {"WWW-Authenticate": "Bearer"}


def test_a_half_configured_pool_is_a_503_without_secret_detail(monkeypatch, caplog):
    monkeypatch.setenv("MERIDIAN_COGNITO_REGION", "us-east-1-secret-marker")
    caplog.set_level(logging.DEBUG)
    response = TestClient(app, client=REMOTE).get(
        "/api/me", headers={"Authorization": "Bearer some-token"})
    assert response.status_code == 503
    assert response.json() == {"error": "Sign-in is misconfigured."}
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "MERIDIAN_COGNITO_USER_POOL_ID" in logged
    assert "secret-marker" not in logged + response.text


def test_a_half_configured_pool_is_a_503_on_loopback_too(monkeypatch):
    monkeypatch.setenv("MERIDIAN_COGNITO_REGION", "us-east-1")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("MERIDIAN_API_TOKEN", raising=False)
    with pytest.raises(HTTPException) as refused:
        authenticate("127.0.0.1", None)
    assert refused.value.status_code == 503


@pytest.mark.parametrize("failure", ["unavailable", "invalid"])
def test_a_failed_verification_never_logs_the_token_or_exception_text(
    failure, monkeypatch, caplog
):
    token = "eyJ.secret-token-value.sig"

    class Stub:
        def verify(self, supplied):
            if failure == "unavailable":
                raise CognitoUnavailable(f"fetch failed for {supplied}")
            exc = InvalidCognitoToken("expired")
            exc.args = (f"rejected {supplied}",)
            raise exc

    monkeypatch.setattr(http_auth, "get_cognito_verifier", lambda: Stub())
    caplog.set_level(logging.DEBUG)
    with pytest.raises(HTTPException) as refused:
        authenticate("203.0.113.8", f"Bearer {token}")
    assert refused.value.status_code == (503 if failure == "unavailable" else 401)
    for record in caplog.records:
        rendered = record.getMessage() + (record.exc_text or "") + repr(record.exc_info)
        assert token not in rendered
        assert "fetch failed" not in rendered and "rejected" not in rendered


@pytest.mark.parametrize("header", ["Bearer ", "Bearer", "Basic x", "Token abc", ""])
def test_an_authorization_header_without_a_bearer_token_is_refused_on_loopback(
    header, cognito, monkeypatch
):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("MERIDIAN_API_TOKEN", raising=False)
    monkeypatch.delenv("MERIDIAN_ALLOW_INSECURE_LOCALHOST", raising=False)
    with pytest.raises(HTTPException) as refused:
        authenticate("127.0.0.1", header)
    assert refused.value.status_code == 401
    assert refused.value.headers == CHALLENGE


@pytest.mark.parametrize(
    ("headers", "status_code"),
    [
        ("lowercase", 200),
        ("uppercase", 200),
        ("two-spaces", 401),
        ("trailing-space-no-scheme", 401),
        ("bare-token", 401),
        ("bad-then-good", 401),
        ("good-then-bad", 200),
    ],
)
def test_authorization_header_shapes_are_pinned(key, cognito, headers, status_code):
    good = token_for(key, DECOY)
    shapes = {
        "lowercase": [f"bearer {good}"],
        "uppercase": [f"BEARER {good}"],
        "two-spaces": [f"Bearer  {good}"],
        "trailing-space-no-scheme": [f"{good} "],
        "bare-token": [good],
        "bad-then-good": ["Bearer not-a-token", f"Bearer {good}"],
        "good-then-bad": [f"Bearer {good}", "Bearer not-a-token"],
    }
    pairs = [("Authorization", value) for value in shapes[headers]]
    response = TestClient(app, client=REMOTE).get("/api/me", headers=pairs)
    assert response.status_code == status_code
    if status_code == 401:
        assert response.headers["www-authenticate"] == "Bearer"


def test_a_token_only_in_the_query_string_is_not_a_credential(key, cognito, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("MERIDIAN_API_TOKEN", raising=False)
    response = TestClient(app, client=REMOTE).get(
        f"/api/me?access_token={token_for(key, DECOY)}")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


class _MemoryStore:
    def __init__(self):
        self.read_for = []

    async def recall_preferences(self, traveler_id, limit=8, transaction_id=None):
        self.read_for.append(traveler_id)
        return []

    async def recall_profile(self, traveler_id, transaction_id=None):
        return None


class _ScopedDb:
    @asynccontextmanager
    async def scoped_session(self, **kwargs):
        yield "txn"


@pytest.fixture
def memory_store(monkeypatch):
    from backend.routers import memory

    store = _MemoryStore()
    monkeypatch.setattr(memory, "get_memory_store", lambda: store)
    monkeypatch.setattr(memory, "get_rds_data_client", lambda: _ScopedDb())
    identity = type("Identity", (), {"authorization_context": lambda self: "authz"})()
    monkeypatch.setattr(memory, "get_agentcore_identity", lambda: identity)
    return store


def test_the_memory_route_resolves_me_to_the_decoys_traveler(key, cognito, memory_store):
    headers = {"Authorization": f"Bearer {token_for(key, DECOY)}"}
    response = TestClient(app).get("/api/memory/me", headers=headers)
    assert response.status_code == 200
    assert response.json()["traveler_id"] == "trv_demo_decoy"
    assert memory_store.read_for == ["trv_demo_decoy"]


def test_the_memory_route_still_refuses_an_explicit_other_traveler(key, cognito, memory_store):
    headers = {"Authorization": f"Bearer {token_for(key, DECOY)}"}
    assert TestClient(app).get("/api/memory/trv_meridian_demo", headers=headers).status_code == 403
    assert memory_store.read_for == []


def test_chat_resolves_me_to_the_callers_traveler_downstream(key, cognito, monkeypatch):
    from backend.routers import chat

    seen = []

    def stop_after_authorization(*args, **kwargs):
        seen.append(kwargs["traveler_id"])
        raise HTTPException(status_code=418, detail="stop")

    monkeypatch.setattr(chat, "log_turn_start", stop_after_authorization)
    headers = {"Authorization": f"Bearer {token_for(key, DECOY)}"}
    response = TestClient(app).post(
        "/api/chat", headers=headers,
        json={"message": "Show me city trips.", "phase": 1, "customer_id": CURRENT_TRAVELER})
    assert response.status_code == 418
    assert seen == ["trv_demo_decoy"]
