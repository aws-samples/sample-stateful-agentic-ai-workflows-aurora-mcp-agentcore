"""HTTP caller identity must be separate from backend workload identity."""

import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from backend.http_auth import (
    HttpPrincipal,
    authorize_traveler,
    require_http_principal,
)


def _request(host: str, headers: dict[str, str] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/memory/trv_meridian_demo",
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
            "client": (host, 12345),
            "server": ("localhost", 8000),
            "scheme": "http",
            "query_string": b"",
        }
    )


def test_loopback_development_caller_is_pinned_to_demo_traveler(monkeypatch):
    monkeypatch.delenv("MERIDIAN_API_TOKEN", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")

    principal = asyncio.run(require_http_principal(_request("127.0.0.1"), None))

    assert principal.authentication == "loopback-development"
    assert principal.traveler_id == "trv_meridian_demo"


@pytest.mark.parametrize(
    "header",
    [
        {"X-Forwarded-For": "127.0.0.1"},
        {"Forwarded": "for=127.0.0.1"},
        {"X-Real-IP": "127.0.0.1"},
    ],
)
def test_forwarded_request_is_never_loopback(monkeypatch, header):
    """Uvicorn rewrites client.host from these headers, so a remote caller can fake 127.0.0.1."""
    monkeypatch.delenv("MERIDIAN_API_TOKEN", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_http_principal(_request("127.0.0.1", header), None))

    assert exc.value.status_code == 503


def test_non_ascii_bearer_token_is_a_401_not_a_500(monkeypatch):
    monkeypatch.setenv("MERIDIAN_API_TOKEN", "expected-token")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_http_principal(_request("203.0.113.8"), "Bearer töken"))

    assert exc.value.status_code == 401


def test_remote_caller_requires_configured_authentication(monkeypatch):
    monkeypatch.delenv("MERIDIAN_API_TOKEN", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_http_principal(_request("203.0.113.8"), None))

    assert exc.value.status_code == 503


def test_bearer_token_authenticates_and_traveler_claim_cannot_change(monkeypatch):
    monkeypatch.setenv("MERIDIAN_API_TOKEN", "test-secret")
    monkeypatch.setenv("MERIDIAN_API_TRAVELER_ID", "trv_meridian_demo")

    principal = asyncio.run(
        require_http_principal(_request("203.0.113.8"), "Bearer test-secret")
    )
    assert principal.authentication == "bearer"
    assert authorize_traveler(principal, "trv_meridian_demo") == "trv_meridian_demo"

    with pytest.raises(HTTPException) as exc:
        authorize_traveler(principal, "trv_demo_decoy")
    assert exc.value.status_code == 403


def test_invalid_bearer_token_is_rejected(monkeypatch):
    monkeypatch.setenv("MERIDIAN_API_TOKEN", "test-secret")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_http_principal(_request("203.0.113.8"), "Bearer wrong"))

    assert exc.value.status_code == 401


def test_authorize_traveler_uses_authenticated_default():
    principal = HttpPrincipal("subject", "trv_meridian_demo", "test")
    assert authorize_traveler(principal, None) == "trv_meridian_demo"
