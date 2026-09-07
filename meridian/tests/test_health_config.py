"""Health endpoint exposes Bedrock / embedding config for the UI."""

import pytest
from fastapi.testclient import TestClient

from backend.config import bedrock_model_label
from backend.main import app, parse_cors_origins


def test_bedrock_model_label_opus():
    assert bedrock_model_label("global.anthropic.claude-opus-4-8") == "Claude Opus 4.8"


def test_bedrock_model_label_sonnet_5():
    assert (
        bedrock_model_label("global.anthropic.claude-sonnet-5")
        == "Claude Sonnet 5"
    )


def test_bedrock_model_label_sonnet_4_5():
    assert (
        bedrock_model_label("global.anthropic.claude-sonnet-4-5-20250929-v1:0")
        == "Claude Sonnet 4.5"
    )


def test_health_includes_model_fields():
    res = TestClient(app).get("/health")
    assert res.status_code == 200
    body = res.json()
    assert "bedrock_model_id" in body
    assert "bedrock_model_label" in body
    assert "embedding_model_id" in body
    assert body["bedrock_model_label"]


def test_cors_origins_accepts_explicit_allowlist():
    assert parse_cors_origins(" https://app.example,https://preview.example ") == [
        "https://app.example",
        "https://preview.example",
    ]


def test_cors_origins_rejects_wildcard():
    with pytest.raises(ValueError, match="wildcard CORS"):
        parse_cors_origins("https://app.example,*")


def test_error_handler_preserves_http_authentication_challenge():
    from fastapi import FastAPI, HTTPException
    from backend.main import http_exception_handler

    sample = FastAPI()
    sample.add_exception_handler(HTTPException, http_exception_handler)

    @sample.get("/protected")
    async def protected():
        raise HTTPException(401, "Sign in required", headers={"WWW-Authenticate": "Bearer"})

    response = TestClient(sample).get("/protected")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json() == {"error": "Sign in required"}


@pytest.mark.parametrize("path", ["/api/packages", "/api/products", "/api/packages/demo", "/api/products/demo"])
def test_catalog_outage_returns_safe_retryable_error(monkeypatch, path):
    import backend.routers.products as products

    async def unavailable(*args, **kwargs):
        raise RuntimeError("internal database endpoint and diagnostic detail")

    monkeypatch.setattr(products, "_list_packages", unavailable)
    monkeypatch.setattr(products, "_get_package", unavailable)
    response = TestClient(app).get(path)
    assert response.status_code == 503
    assert response.json() == {"error": products.CATALOG_UNAVAILABLE}
    assert "internal database" not in response.text
