"""The verified token reaches the AgentCore clients without being passed through every route."""

import asyncio
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from backend import http_auth
from backend.agentcore.caller_credential import (
    CallerCredentialMiddleware,
    bearer_from_headers,
    bind_caller_token,
    caller_token_scope,
    current_caller_token,
    require_caller_token,
)
from backend.agentcore.errors import CallerTokenMissing
from backend.cognito_auth import CognitoConfig, CognitoVerifier
from backend.http_auth import require_http_principal

CONFIG = CognitoConfig("us-east-1", "us-east-1_AbCdEfGhI", "client-web")


@pytest.mark.parametrize(("headers", "expected"), [
    ({"Authorization": "Bearer abc.def.ghi"}, "abc.def.ghi"),
    ({"authorization": "bearer abc"}, "abc"),
    ({"AUTHORIZATION": "Bearer   padded  "}, "padded"),
    ({"Authorization": "Basic abc"}, None),
    ({"Authorization": "Bearer"}, None),
    ({"Authorization": "Bearer   "}, None),
    ({"X-Other": "Bearer abc"}, None),
    ({}, None),
    (None, None),
])
def test_the_bearer_is_read_case_insensitively_or_not_at_all(headers, expected):
    assert bearer_from_headers(headers) == expected


def test_nothing_is_bound_outside_a_request():
    assert current_caller_token() is None
    with pytest.raises(CallerTokenMissing, match="caller_token_scope"):
        require_caller_token()


def test_a_scope_binds_for_its_block_only():
    with caller_token_scope("token-a"):
        assert require_caller_token() == "token-a"
    assert current_caller_token() is None


async def test_tasks_and_threads_started_before_the_bind_still_see_it():
    with caller_token_scope(None):
        task = asyncio.ensure_future(asyncio.sleep(0, result="x"))
        bind_caller_token("late-token")
        seen_by_thread = await asyncio.to_thread(current_caller_token)
        await task
    assert seen_by_thread == "late-token"


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_token(key, **overrides):
    now = int(time.time())
    claims = {"sub": "sub-j", "iss": CONFIG.issuer, "client_id": "client-web",
              "token_use": "access", "traveler_id": "trv_meridian_demo",
              "iat": now, "exp": now + 3600, **overrides}
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture
def client(key, monkeypatch):
    verifier = CognitoVerifier(CONFIG, signing_key_for=lambda token: key.public_key())
    monkeypatch.setattr(http_auth, "get_cognito_verifier", lambda: verifier)
    app = FastAPI()
    app.add_middleware(CallerCredentialMiddleware)

    @app.get("/probe", dependencies=[Depends(require_http_principal)])
    async def probe():
        in_thread = await asyncio.to_thread(current_caller_token)
        in_task = await asyncio.ensure_future(_read())
        return {"direct": current_caller_token(), "thread": in_thread, "task": in_task}

    @app.get("/stream", dependencies=[Depends(require_http_principal)])
    async def stream():
        async def body():
            yield (await asyncio.ensure_future(_read())) or "none"

        return StreamingResponse(body(), media_type="text/plain")

    @app.get("/open")
    async def open_route():
        return {"direct": current_caller_token()}

    return TestClient(app)


async def _read():
    return current_caller_token()


def test_the_seam_binds_the_verified_token_for_the_route_its_threads_and_tasks(client, key):
    token = make_token(key)
    body = client.get("/probe", headers={"Authorization": f"Bearer {token}"}).json()
    assert body == {"direct": token, "thread": token, "task": token}


def test_a_streamed_response_still_sees_the_token(client, key):
    token = make_token(key)
    assert client.get("/stream", headers={"Authorization": f"Bearer {token}"}).text == token


def test_the_next_request_never_sees_the_previous_callers_token(client, key):
    client.get("/probe", headers={"Authorization": f"Bearer {make_token(key)}"})
    assert client.get("/open").json() == {"direct": None}


def test_a_refused_token_binds_nothing(client, key):
    expired = make_token(key, exp=int(time.time()) - 3600)
    assert client.get("/probe", headers={"Authorization": f"Bearer {expired}"}).status_code == 401
    assert client.get("/open").json() == {"direct": None}
