"""Health endpoint exposes Bedrock / embedding config for the UI."""

from fastapi.testclient import TestClient

from backend.config import bedrock_model_label
from backend.main import app


def test_bedrock_model_label_opus():
    assert bedrock_model_label("global.anthropic.claude-opus-4-8") == "Claude Opus 4.8"


def test_bedrock_model_label_sonnet():
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
