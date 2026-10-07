"""The deployed user pool: real sign-ins, the trigger claim and the API verdict on each token."""

import base64
import json
import os
import uuid

import boto3
import jwt
import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from backend.cognito_auth import (
    CognitoConfig,
    CognitoVerifier,
    InvalidCognitoToken,
    cognito_config_from_env,
)
from backend.main import app
from scripts.cognito_tokens import mint_tokens

pytestmark = pytest.mark.database

JORDAN = "trv_meridian_demo"
DECOY = "trv_demo_decoy"
SETTINGS = (
    "MERIDIAN_COGNITO_REGION", "MERIDIAN_COGNITO_USER_POOL_ID", "MERIDIAN_COGNITO_APP_CLIENT_ID",
)


@pytest.fixture(scope="module")
def config() -> CognitoConfig:
    missing = [name for name in SETTINGS if not os.environ.get(name)]
    if missing:
        pytest.fail(f"{', '.join(missing)} not set: run scripts/sync_cognito_env.py --write")
    return cognito_config_from_env()


@pytest.fixture(scope="module")
def tokens(config):
    return {"jordan": mint_tokens("jordan"), "decoy": mint_tokens("decoy")}


@pytest.fixture
def verifier(config):
    return CognitoVerifier(config)


def claims_of(token: str) -> dict:
    return jwt.decode(token, options={"verify_signature": False})


def bearer(tokens, who: str) -> dict:
    return {"Authorization": f"Bearer {tokens[who]['access']}"}


@pytest.mark.parametrize(("who", "traveler"), [("jordan", JORDAN), ("decoy", DECOY)])
def test_the_trigger_puts_the_bound_traveler_in_the_access_token(tokens, config, who, traveler):
    claims = claims_of(tokens[who]["access"])
    assert claims["traveler_id"] == traveler
    assert claims["token_use"] == "access"
    assert claims["client_id"] == config.app_client_id
    assert claims["exp"] - claims["iat"] == 3600


def test_the_id_token_names_the_person_for_the_page(tokens):
    assert claims_of(tokens["jordan"]["id"])["name"] == "Jordan Morgan"
    assert claims_of(tokens["decoy"]["id"])["name"] == "Jordan Lee"
    assert "picture" in claims_of(tokens["jordan"]["id"])
    assert "picture" not in claims_of(tokens["decoy"]["id"])


@pytest.mark.parametrize(("who", "traveler"), [("jordan", JORDAN), ("decoy", DECOY)])
def test_the_backend_verifies_each_real_token_against_the_pools_keys(
    verifier, tokens, who, traveler
):
    identity = verifier.verify(tokens[who]["access"])
    assert identity.traveler_id == traveler
    assert identity.subject_id == claims_of(tokens[who]["access"])["sub"]


def test_the_id_token_is_refused_where_an_access_token_is_required(verifier, tokens):
    with pytest.raises(InvalidCognitoToken) as refused:
        verifier.verify(tokens["jordan"]["id"])
    assert refused.value.reason == "token_use"


def test_another_app_client_is_refused(config, tokens):
    other = CognitoVerifier(CognitoConfig(config.region, config.user_pool_id, "another-client"))
    with pytest.raises(InvalidCognitoToken) as refused:
        other.verify(tokens["jordan"]["access"])
    assert refused.value.reason == "client_id"


def test_a_decoy_token_with_jordans_traveler_written_in_fails_its_signature(verifier, tokens):
    header, payload, signature = tokens["decoy"]["access"].split(".")
    forged = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    forged["traveler_id"] = JORDAN
    body = base64.urlsafe_b64encode(json.dumps(forged).encode()).rstrip(b"=").decode()
    with pytest.raises(InvalidCognitoToken) as refused:
        verifier.verify(f"{header}.{body}.{signature}")
    assert refused.value.reason == "signature"


def test_the_api_names_each_signed_in_traveler(tokens, config):
    client = TestClient(app)
    for who, traveler in (("jordan", JORDAN), ("decoy", DECOY)):
        body = client.get("/api/me", headers=bearer(tokens, who)).json()
        assert body == {"traveler_id": traveler, "authentication": "cognito"}


def test_the_decoy_is_refused_jordans_records_by_id(tokens, config):
    client = TestClient(app)
    for path in (f"/api/memory/{JORDAN}", f"/api/journeys?traveler_id={JORDAN}"):
        response = client.get(path, headers=bearer(tokens, "decoy"))
        assert response.status_code == 403, path
    chat = client.post("/api/chat", headers=bearer(tokens, "decoy"),
                       json={"message": "Show me city trips", "phase": 1, "customer_id": JORDAN})
    assert chat.status_code == 403


def test_jordans_own_token_reads_jordans_records(tokens, config):
    client = TestClient(app)
    response = client.get(f"/api/memory/{JORDAN}", headers=bearer(tokens, "jordan"))
    assert response.status_code == 200
    assert response.json()["traveler_id"] == JORDAN
    alias = client.get("/api/memory/me", headers=bearer(tokens, "jordan"))
    assert alias.json()["traveler_id"] == JORDAN


def test_the_workload_grant_is_a_second_check_the_decoy_also_meets(tokens, config):
    response = TestClient(app).get("/api/memory/me", headers=bearer(tokens, "decoy"))
    assert response.status_code == 403
    assert "workload" in response.json()["error"]


def test_a_request_without_a_token_is_refused(config, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("MERIDIAN_API_TOKEN", raising=False)
    response = TestClient(app, client=("203.0.113.8", 4000)).get("/api/me")
    assert response.status_code == 401


def test_a_user_without_a_traveler_binding_cannot_sign_in(config):
    idp = boto3.client("cognito-idp", region_name=config.region)
    email = f"itest-{uuid.uuid4().hex[:8]}@example.test"
    password = uuid.uuid4().hex + "-Aa1"
    idp.admin_create_user(UserPoolId=config.user_pool_id, Username=email, MessageAction="SUPPRESS",
                          UserAttributes=[{"Name": "email", "Value": email},
                                          {"Name": "email_verified", "Value": "true"}])
    try:
        idp.admin_set_user_password(UserPoolId=config.user_pool_id, Username=email,
                                    Password=password, Permanent=True)
        with pytest.raises(ClientError) as refused:
            idp.admin_initiate_auth(
                UserPoolId=config.user_pool_id, ClientId=config.app_client_id,
                AuthFlow="ADMIN_USER_PASSWORD_AUTH",
                AuthParameters={"USERNAME": email, "PASSWORD": password})
        assert refused.value.response["Error"]["Code"] == "UserLambdaValidationException"
    finally:
        idp.admin_delete_user(UserPoolId=config.user_pool_id, Username=email)
