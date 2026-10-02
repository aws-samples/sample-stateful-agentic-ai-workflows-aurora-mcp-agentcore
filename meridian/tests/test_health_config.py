"""Health endpoint exposes Bedrock / embedding config for the UI."""

import pytest
from fastapi.testclient import TestClient

from backend.config import bedrock_model_label
from backend.main import app, parse_cors_origins


PROFILE_ARN = "arn:aws:bedrock:us-east-1:123456789012:inference-profile/"


@pytest.mark.parametrize(
    ("model_id", "label"),
    [
        ("global.anthropic.claude-sonnet-5", "Claude Sonnet 5"),
        ("us.anthropic.claude-sonnet-5", "Claude Sonnet 5"),
        (f"{PROFILE_ARN}global.anthropic.claude-sonnet-5", "Claude Sonnet 5"),
        ("global.anthropic.claude-haiku-4-5-20251001-v1:0", "Claude Haiku 4.5"),
        ("global.anthropic.claude-opus-5", "Claude Opus 5"),
        ("global.anthropic.claude-sonnet-5-5", "Claude Sonnet 5.5"),
        ("us.openai.gpt-6-luna", "GPT-6 Luna"),
        ("us.openai.gpt-6-sol", "GPT-6 Sol"),
        ("global.openai.gpt-6-sol", "GPT-6 Sol"),
        ("us.openai.gpt-6.1-sol", "GPT-6.1 Sol"),
        ("global.openai.gpt-6-luna", "GPT-6 Luna"),
        (f"{PROFILE_ARN}us.openai.gpt-6-luna", "GPT-6 Luna"),
        ("us.openai.unknown-model", "us.openai.unknown-model"),
        ("global.anthropic.claude-opus-5-5", "global.anthropic.claude-opus-5-5"),
        ("global.anthropic.claude-opus-4-8", "global.anthropic.claude-opus-4-8"),
        ("global.anthropic.claude-sonnet-4-5-20250929-v1:0",
         "global.anthropic.claude-sonnet-4-5-20250929-v1:0"),
    ],
)
def test_bedrock_model_label_names_known_models_and_preserves_unknown_ids(model_id, label):
    assert bedrock_model_label(model_id) == label


def test_health_includes_model_fields(monkeypatch):
    from backend import health_probe

    async def fake_probe():
        return health_probe.AuroraProbeResult(ok=True)

    monkeypatch.setattr(health_probe, "probe_aurora", fake_probe)
    res = TestClient(app).get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert "bedrock_model_id" in body
    assert "bedrock_model_label" in body
    assert "embedding_model_id" in body
    assert body["bedrock_model_label"]


def test_health_reports_healthy_when_aurora_probe_passes(monkeypatch):
    from backend import health_probe

    async def fake_probe():
        return health_probe.AuroraProbeResult(ok=True)

    monkeypatch.setattr(health_probe, "probe_aurora", fake_probe)
    body = TestClient(app).get("/api/health").json()

    assert body["status"] == "healthy"
    assert body["aurora_reachable"] is True
    assert body["degraded_component"] is None
    assert body["degraded_error_class"] is None


def test_health_reports_degraded_with_component_and_error_class_when_aurora_is_down(monkeypatch):
    from backend import health_probe

    async def fake_probe():
        return health_probe.AuroraProbeResult(ok=False, error_class="ExpiredTokenException")

    monkeypatch.setattr(health_probe, "probe_aurora", fake_probe)
    res = TestClient(app).get("/api/health")

    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "degraded"
    assert body["aurora_reachable"] is False
    assert body["degraded_component"] == "aurora"
    assert body["degraded_error_class"] == "ExpiredTokenException"
    # Existing fields callers already read must still be present.
    assert body["checkpoint_backend"]
    assert "checkpoint_durable" in body
    assert "checkpoint_required" in body


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


@pytest.mark.parametrize("path", ["/api/products", "/api/packages", "/api/products/demo", "/api/packages/demo", "/openapi.json", "/docs", "/redoc", "/api/health", "/"])
def test_origin_routes_require_authentication(monkeypatch, path):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("MERIDIAN_ALLOW_INSECURE_LOCALHOST", "false")
    monkeypatch.delenv("MERIDIAN_API_TOKEN", raising=False)
    response = TestClient(app).get(path)
    assert response.status_code == 503
    assert "HTTP authentication is not configured" in response.json()["error"]


def test_public_liveness_does_not_expose_configuration(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert TestClient(app).get("/health").json() == {"status": "healthy"}
