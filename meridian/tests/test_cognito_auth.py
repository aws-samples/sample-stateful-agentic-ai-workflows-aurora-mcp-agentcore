"""A Cognito access token proves a traveler only if every check passes."""

import base64
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.exceptions import PyJWKClientConnectionError

from backend import cognito_auth
from backend.cognito_auth import (
    CognitoConfig,
    CognitoUnavailable,
    CognitoVerifier,
    InvalidCognitoToken,
)

REGION = "us-east-1"
POOL = "us-east-1_AbCdEfGhI"
CLIENT = "client-web"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{POOL}"
CONFIG = CognitoConfig(REGION, POOL, CLIENT)


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def signing_key():
    return _key()


@pytest.fixture(scope="module")
def other_key():
    return _key()


def _pem(key):
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def claims(**overrides):
    now = int(time.time())
    base = {
        "sub": "11111111-2222-3333-4444-555555555555",
        "iss": ISSUER,
        "client_id": CLIENT,
        "token_use": "access",
        "username": "jordan",
        "traveler_id": "trv_meridian_demo",
        "iat": now,
        "exp": now + 3600,
    }
    base.update(overrides)
    return {key: value for key, value in base.items() if value is not None}


def mint(key, **overrides):
    return jwt.encode(claims(**overrides), _pem(key), algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture
def verifier(signing_key):
    return CognitoVerifier(CONFIG, signing_key_for=lambda token: signing_key.public_key())


def reason_of(verifier, token):
    with pytest.raises(InvalidCognitoToken) as raised:
        verifier.verify(token)
    return raised.value.reason


def test_a_valid_access_token_proves_the_traveler(verifier, signing_key):
    identity = verifier.verify(mint(signing_key))
    assert identity.traveler_id == "trv_meridian_demo"
    assert identity.subject_id == "11111111-2222-3333-4444-555555555555"
    assert identity.username == "jordan"


def test_an_expired_token_is_refused(verifier, signing_key):
    assert reason_of(verifier, mint(signing_key, exp=int(time.time()) - 3600)) == "expired"


def test_a_token_from_another_issuer_is_refused(verifier, signing_key):
    token = mint(signing_key, iss=f"https://cognito-idp.{REGION}.amazonaws.com/us-east-1_Other")
    assert reason_of(verifier, token) == "issuer"


def test_a_token_for_another_app_client_is_refused(verifier, signing_key):
    assert reason_of(verifier, mint(signing_key, client_id="someone-elses-client")) == "client_id"


def test_an_id_token_is_refused_where_an_access_token_is_required(verifier, signing_key):
    id_token = mint(
        signing_key, token_use="id", client_id=None, aud=CLIENT, email="jordan@example.test"
    )
    assert reason_of(verifier, id_token) == "token_use"


def test_a_token_without_the_traveler_claim_is_refused(verifier, signing_key):
    assert reason_of(verifier, mint(signing_key, traveler_id=None)) == "traveler_claim"


@pytest.mark.parametrize("value", ["", "has space", "x" * 51, "trv/../other", 7, ["trv_a"]])
def test_a_malformed_traveler_claim_is_refused(verifier, signing_key, value):
    assert reason_of(verifier, mint(signing_key, traveler_id=value)) == "traveler_claim"


def test_a_token_without_an_expiry_is_refused(verifier, signing_key):
    assert reason_of(verifier, mint(signing_key, exp=None)) == "missing_claim"


def test_a_token_signed_by_another_key_is_refused(verifier, other_key):
    assert reason_of(verifier, mint(other_key)) == "signature"


def test_a_forged_traveler_claim_breaks_the_signature(verifier, signing_key):
    header, payload, signature = mint(signing_key).split(".")
    forged = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    forged["traveler_id"] = "trv_demo_decoy"
    body = base64.urlsafe_b64encode(json.dumps(forged).encode()).rstrip(b"=").decode()
    assert reason_of(verifier, ".".join([header, body, signature])) == "signature"


def test_an_unsigned_token_is_refused(verifier):
    unsigned = jwt.encode(claims(), None, algorithm="none", headers={"kid": "k1"})
    assert reason_of(verifier, unsigned) == "algorithm"


def test_a_symmetric_token_is_refused(verifier):
    token = jwt.encode(claims(), "x" * 64, algorithm="HS256", headers={"kid": "k1"})
    assert reason_of(verifier, token) == "algorithm"


@pytest.mark.parametrize("token", ["", "not-a-token", "a.b.c", "Bearer abc"])
def test_garbage_is_refused_as_malformed(verifier, token):
    assert reason_of(verifier, token) == "malformed"


def test_unreachable_signing_keys_are_unavailable_not_invalid(signing_key):
    def unreachable(token):
        raise PyJWKClientConnectionError("network down")

    broken = CognitoVerifier(CONFIG, signing_key_for=unreachable)
    with pytest.raises(CognitoUnavailable):
        broken.verify(mint(signing_key))


def test_a_token_whose_key_is_unknown_is_refused(signing_key):
    def unknown(token):
        raise jwt.PyJWKClientError("Unable to find a signing key that matches: 'k9'")

    verifier = CognitoVerifier(CONFIG, signing_key_for=unknown)
    assert reason_of(verifier, mint(signing_key)) == "signing_key"


def test_the_reason_never_contains_the_token(verifier, signing_key):
    token = mint(signing_key, exp=int(time.time()) - 3600)
    assert token not in reason_of(verifier, token)


def test_the_issuer_and_keys_url_follow_the_pool():
    assert CONFIG.issuer == ISSUER
    assert CONFIG.jwks_url == f"{ISSUER}/.well-known/jwks.json"


def test_no_settings_means_no_verifier(monkeypatch):
    for key in cognito_auth.ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    assert cognito_auth.cognito_config_from_env() is None
    assert cognito_auth.get_cognito_verifier() is None


def test_all_three_settings_make_one_shared_verifier(monkeypatch):
    for key, value in zip(cognito_auth.ENV_KEYS, (REGION, POOL, CLIENT), strict=True):
        monkeypatch.setenv(key, value)
    assert cognito_auth.cognito_config_from_env() == CONFIG
    first = cognito_auth.get_cognito_verifier()
    assert first is cognito_auth.get_cognito_verifier()


def test_a_half_configured_pool_fails_fast_and_names_the_gap(monkeypatch):
    for key in cognito_auth.ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MERIDIAN_COGNITO_REGION", REGION)
    with pytest.raises(RuntimeError, match="MERIDIAN_COGNITO_USER_POOL_ID"):
        cognito_auth.cognito_config_from_env()
