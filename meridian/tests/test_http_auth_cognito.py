"""The API seam accepts a verified Cognito identity and never lets a caller choose its traveler."""

import asyncio
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend import http_auth
from backend.cognito_auth import CognitoConfig, CognitoUnavailable, CognitoVerifier
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
