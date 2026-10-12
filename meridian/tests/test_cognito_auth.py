"""A Cognito access token proves a traveler only if every check passes."""

import base64
import hashlib
import hmac
import json
import threading
import time
from urllib.error import URLError

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt import PyJWKClient
from jwt.algorithms import RSAAlgorithm
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


def mint(key, *, kid="k1", algorithm="RS256", headers=None, **overrides):
    all_headers = dict(headers or {})
    if kid is not None:
        all_headers["kid"] = kid
    return jwt.encode(claims(**overrides), _pem(key), algorithm=algorithm, headers=all_headers)


@pytest.fixture(autouse=True)
def fresh_verifier_cache():
    cognito_auth._verifier_for.cache_clear()
    yield
    cognito_auth._verifier_for.cache_clear()


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


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _hs256_token(secret):
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "k1"}).encode())
    payload = _b64(json.dumps(claims()).encode())
    signing_input = f"{header}.{payload}".encode()
    return f"{header}.{payload}.{_b64(hmac.new(secret, signing_input, hashlib.sha256).digest())}"


def test_only_rs256_is_allowed():
    assert cognito_auth.ALGORITHMS == ["RS256"]


def test_hs256_signed_with_the_public_key_as_secret_is_refused(verifier, signing_key):
    public_pem = signing_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    assert reason_of(verifier, _hs256_token(public_pem)) == "algorithm"


def test_hs256_signed_with_the_jwk_modulus_as_secret_is_refused(verifier, signing_key):
    modulus = signing_key.public_key().public_numbers().n
    secret = modulus.to_bytes((modulus.bit_length() + 7) // 8, "big")
    assert reason_of(verifier, _hs256_token(secret)) == "algorithm"


@pytest.mark.parametrize("algorithm", ["RS512", "PS256"])
def test_other_asymmetric_algorithms_are_refused(verifier, signing_key, algorithm):
    assert reason_of(verifier, mint(signing_key, algorithm=algorithm)) == "algorithm"


@pytest.mark.parametrize(
    "iss_suffix_or_value",
    [ISSUER + "x", ISSUER + "/", ISSUER[:-1], "https://evil.test/?" + ISSUER, ISSUER.upper()],
)
def test_only_the_exact_issuer_is_accepted(verifier, signing_key, iss_suffix_or_value):
    assert reason_of(verifier, mint(signing_key, iss=iss_suffix_or_value)) == "issuer"


@pytest.mark.parametrize("value", [[CLIENT], {"id": CLIENT}, CLIENT + "x", CLIENT.upper(), 7])
def test_the_app_client_must_be_the_exact_string(verifier, signing_key, value):
    assert reason_of(verifier, mint(signing_key, client_id=value)) == "client_id"


@pytest.mark.parametrize("claim", ["nbf", "iat"])
def test_a_token_from_the_future_is_refused(verifier, signing_key, claim):
    token = mint(signing_key, **{claim: int(time.time()) + 3600})
    assert reason_of(verifier, token) == "not_yet_valid"


@pytest.mark.parametrize("claim", ["nbf", "iat"])
def test_clock_skew_within_the_leeway_is_accepted(verifier, signing_key, claim):
    assert verifier.verify(mint(signing_key, **{claim: int(time.time()) + 20}))


@pytest.mark.parametrize("claim", ["nbf", "iat"])
def test_clock_skew_beyond_the_leeway_is_refused(verifier, signing_key, claim):
    token = mint(signing_key, **{claim: int(time.time()) + 40})
    assert reason_of(verifier, token) == "not_yet_valid"


def test_a_token_that_expired_within_the_leeway_is_accepted(verifier, signing_key):
    assert verifier.verify(mint(signing_key, exp=int(time.time()) - 20))


def test_a_token_that_expired_beyond_the_leeway_is_refused(verifier, signing_key):
    assert reason_of(verifier, mint(signing_key, exp=int(time.time()) - 40)) == "expired"


@pytest.mark.parametrize("value", [None, "", 5, ["a"], {"a": 1}])
def test_a_token_without_a_usable_subject_is_refused(verifier, signing_key, value):
    assert reason_of(verifier, mint(signing_key, sub=value)) == "missing_claim"


@pytest.mark.parametrize("value", [["jordan"], 7, {"a": 1}, True])
def test_a_username_that_is_not_text_is_dropped(verifier, signing_key, value):
    assert verifier.verify(mint(signing_key, username=value)).username is None


def test_a_token_without_a_username_still_verifies(verifier, signing_key):
    assert verifier.verify(mint(signing_key, username=None)).username is None


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class JwksEndpoint:
    """Stands in for the network under PyJWKClient.fetch_data, keeping its caching effects."""

    def __init__(self, clock):
        self.clock = clock
        self.keys = {}
        self.error = None
        self.attempts = 0
        self.delay = 0.0
        self.urls = []

    def serve(self, **public_keys):
        self.keys = public_keys

    def fetch(self, client):
        self.attempts += 1
        self.urls.append(client.uri)
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        data = {
            "keys": [
                {**RSAAlgorithm.to_jwk(key, as_dict=True), "kid": kid, "use": "sig", "alg": "RS256"}
                for kid, key in self.keys.items()
            ]
        }
        client.jwk_set_cache.put(data)
        client._last_successful_fetch = self.clock()
        return data


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(time, "monotonic", fake)
    return fake


@pytest.fixture
def endpoint(monkeypatch, clock, signing_key):
    jwks = JwksEndpoint(clock)

    def fetch_data(client):
        return jwks.fetch(client)

    monkeypatch.setattr(PyJWKClient, "fetch_data", fetch_data)
    jwks.serve(k1=signing_key.public_key())
    return jwks


@pytest.fixture
def live(endpoint):
    return CognitoVerifier(CONFIG)


def test_a_token_verifies_through_the_default_key_path(live, endpoint, signing_key):
    assert live.verify(mint(signing_key)).traveler_id == "trv_meridian_demo"
    assert endpoint.urls == [CONFIG.jwks_url]


def test_a_rotated_out_key_stops_being_trusted_once_the_key_set_expires(
    live, endpoint, clock, signing_key, other_key
):
    live.verify(mint(signing_key))
    endpoint.serve(k2=other_key.public_key())
    clock.advance(cognito_auth.JWKS_LIFESPAN_SECONDS + 1)
    assert reason_of(live, mint(signing_key)) == "signing_key"
    assert live.verify(mint(other_key, kid="k2")).traveler_id == "trv_meridian_demo"


def test_a_jwks_outage_after_expiry_does_not_keep_trusting_cached_keys(
    live, endpoint, clock, signing_key
):
    live.verify(mint(signing_key))
    endpoint.error = PyJWKClientConnectionError("down")
    clock.advance(cognito_auth.JWKS_LIFESPAN_SECONDS + 1)
    with pytest.raises(CognitoUnavailable):
        live.verify(mint(signing_key))


def test_unknown_key_ids_trigger_at_most_one_fetch(live, endpoint, signing_key):
    for index in range(100):
        assert reason_of(live, mint(signing_key, kid=f"unknown-{index}")) == "signing_key"
    assert endpoint.attempts == 1


def test_a_token_without_a_key_id_is_refused(live, signing_key):
    assert reason_of(live, mint(signing_key, kid=None)) == "signing_key"


def test_a_jku_header_does_not_change_where_keys_are_fetched(live, endpoint, signing_key):
    token = mint(signing_key, headers={"jku": "https://evil.test/jwks.json"})
    live.verify(token)
    assert endpoint.urls == [CONFIG.jwks_url]


def test_an_empty_key_set_is_refused_as_a_signing_key_problem(live, endpoint, signing_key):
    endpoint.serve()
    assert reason_of(live, mint(signing_key)) == "signing_key"


@pytest.mark.parametrize(
    "error",
    [ValueError("Expecting value"), ConnectionResetError("reset"), URLError("unreachable")],
)
def test_a_jwks_fetch_failing_in_transport_is_unavailable(live, endpoint, signing_key, error):
    endpoint.error = error
    with pytest.raises(CognitoUnavailable):
        live.verify(mint(signing_key))
    assert endpoint.attempts == 1


def test_a_value_error_from_decoding_is_not_unavailable(monkeypatch, signing_key, verifier):
    def broken_decode(*args, **kwargs):
        raise ValueError("not a key lookup problem")

    monkeypatch.setattr(jwt, "decode", broken_decode)
    with pytest.raises(ValueError):
        verifier.verify(mint(signing_key))


def test_a_down_endpoint_is_not_retried_during_the_backoff_window(
    live, endpoint, clock, signing_key
):
    endpoint.error = PyJWKClientConnectionError("down")
    for _ in range(3):
        with pytest.raises(CognitoUnavailable):
            live.verify(mint(signing_key))
    assert endpoint.attempts == 1


def test_the_endpoint_is_retried_after_the_backoff_window(live, endpoint, clock, signing_key):
    endpoint.error = PyJWKClientConnectionError("down")
    with pytest.raises(CognitoUnavailable):
        live.verify(mint(signing_key))
    clock.advance(cognito_auth.UNAVAILABLE_BACKOFF_SECONDS + 1)
    with pytest.raises(CognitoUnavailable):
        live.verify(mint(signing_key))
    assert endpoint.attempts == 2
    endpoint.error = None
    clock.advance(cognito_auth.UNAVAILABLE_BACKOFF_SECONDS + 1)
    assert live.verify(mint(signing_key)).traveler_id == "trv_meridian_demo"


@pytest.mark.parametrize(
    "token",
    [
        "",
        "not-a-token",
        "a.b.c",
        "\u00e9.\u00e9.\u00e9",
        "e30.e30.e30",
        "W10.W10.W10",
        "\ud800.e30.e30",
        "e30.\ud800.e30",
        "e30.e30.\ud800",
    ],
)
def test_hostile_tokens_never_open_the_backoff_window(live, endpoint, signing_key, token):
    assert reason_of(live, token) in {"malformed", "signing_key", "invalid"}
    assert live.verify(mint(signing_key)).traveler_id == "trv_meridian_demo"


@pytest.mark.parametrize("token", ["\ud800.e30.e30", "e30.\ud800.e30", "e30.e30.\ud800"])
def test_a_non_ascii_token_is_malformed_before_any_key_lookup(live, endpoint, signing_key, token):
    assert reason_of(live, token) == "malformed"
    assert endpoint.attempts == 0
    assert live.verify(mint(signing_key)).traveler_id == "trv_meridian_demo"


def test_a_valid_cached_key_keeps_verifying_while_a_refresh_is_backing_off(
    live, endpoint, clock, signing_key
):
    live.verify(mint(signing_key))
    clock.advance(cognito_auth.JWKS_REFETCH_COOLDOWN_SECONDS + 1)
    endpoint.error = ConnectionResetError("blip")
    with pytest.raises(CognitoUnavailable):
        live.verify(mint(signing_key, kid="unknown"))
    attempts = endpoint.attempts
    assert live.verify(mint(signing_key)).traveler_id == "trv_meridian_demo"
    assert endpoint.attempts == attempts


def test_the_backoff_window_blocks_fetches_only(live, endpoint, clock, signing_key):
    endpoint.error = ConnectionResetError("down")
    for kid in ("unknown-1", "unknown-2"):
        with pytest.raises(CognitoUnavailable):
            live.verify(mint(signing_key, kid=kid))
    assert endpoint.attempts == 1
    clock.advance(cognito_auth.UNAVAILABLE_BACKOFF_SECONDS + 1)
    with pytest.raises(CognitoUnavailable):
        live.verify(mint(signing_key, kid="unknown-3"))
    assert endpoint.attempts == 2


def test_concurrent_requests_at_outage_onset_make_one_fetch_attempt(live, endpoint, signing_key):
    endpoint.error = ConnectionResetError("down")
    endpoint.delay = 0.05
    outcomes = []
    start = threading.Barrier(10)

    def attempt(index):
        token = mint(signing_key, kid=f"unknown-{index}")
        start.wait()
        try:
            live.verify(token)
        except CognitoUnavailable:
            outcomes.append("unavailable")

    threads = [threading.Thread(target=attempt, args=(index,)) for index in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert outcomes == ["unavailable"] * 10
    assert endpoint.attempts == 1
