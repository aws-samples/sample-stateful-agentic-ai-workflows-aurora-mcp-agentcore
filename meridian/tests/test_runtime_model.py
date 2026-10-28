"""The Runtime's model factory must follow Bedrock's contract for Claude Sonnet 5.5."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest

RUNTIME = Path(__file__).resolve().parents[1] / "meridian_agentcore" / "app" / "MeridianConcierge"
spec = importlib.util.spec_from_file_location("concierge_model_loader", RUNTIME / "model" / "load.py")
loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loader)


@pytest.fixture
def model_factory(monkeypatch):
    for name in ("BEDROCK_MODEL_ID", "BEDROCK_MAX_TOKENS", "AWS_REGION"):
        monkeypatch.delenv(name, raising=False)
    factory = Mock()
    monkeypatch.setattr(loader, "BedrockModel", factory)
    return factory


def test_the_default_is_sonnet_5_5_with_no_temperature_and_adaptive_retries(model_factory):
    loader.load_model()

    kwargs = model_factory.call_args.kwargs
    assert kwargs["model_id"] == "global.anthropic.claude-sonnet-5-5"
    assert kwargs["region_name"] == "us-east-1" and kwargs["max_tokens"] == 16000
    assert "temperature" not in kwargs
    assert kwargs["boto_client_config"].retries["mode"] == "adaptive"
    assert kwargs.get("additional_request_fields", {}) == loader.REQUEST_FIELDS


@pytest.mark.parametrize("model_id", ["openai.gpt-oss-20b-1:0", "openai.gpt-oss-120b-1:0"])
def test_oss_models_are_rejected_before_contacting_bedrock(model_factory, model_id):
    with pytest.raises(ValueError, match="GPT-OSS is not supported"):
        loader.load_model(model_id)
    model_factory.assert_not_called()


def test_explicit_overrides_are_honored(model_factory, monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-5-5")
    monkeypatch.setenv("BEDROCK_MAX_TOKENS", "4096")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    model_id = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

    loader.load_model(model_id, "us-east-1")

    kwargs = model_factory.call_args.kwargs
    assert (kwargs["model_id"], kwargs["region_name"], kwargs["max_tokens"]) == (
        model_id, "us-east-1", 4096)


@pytest.mark.parametrize("budget", ["0", "255", "4095", "16001", "not-a-number"])
def test_invalid_output_budget_fails_before_contacting_bedrock(model_factory, monkeypatch, budget):
    monkeypatch.setenv("BEDROCK_MAX_TOKENS", budget)
    with pytest.raises(ValueError):
        loader.load_model()
    model_factory.assert_not_called()


def test_the_budget_floor_is_named_in_the_error_and_is_accepted_exactly(model_factory, monkeypatch):
    monkeypatch.setenv("BEDROCK_MAX_TOKENS", "4095")
    with pytest.raises(ValueError, match=r"BEDROCK_MAX_TOKENS must be between 4096 and 16000"):
        loader.load_model()
    monkeypatch.setenv("BEDROCK_MAX_TOKENS", "4096")
    loader.load_model()
    assert model_factory.call_args.kwargs["max_tokens"] == 4096
