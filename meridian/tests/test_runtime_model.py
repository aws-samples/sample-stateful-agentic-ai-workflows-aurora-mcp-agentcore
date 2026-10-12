"""Provider-specific request fields must match Bedrock's model API contracts."""

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
    for name in ("BEDROCK_MODEL_ID", "BEDROCK_MAX_TOKENS", "BEDROCK_REASONING_EFFORT", "AWS_REGION"):
        monkeypatch.delenv(name, raising=False)
    factory = Mock()
    monkeypatch.setattr(loader, "BedrockModel", factory)
    return factory


def test_default_model_uses_hosted_gpt_reasoning_contract(model_factory):
    loader.load_model()
    model_factory.assert_called_once_with(
        model_id="us.openai.gpt-6-sol", region_name="us-east-1", max_tokens=2048,
        additional_request_fields={"reasoning": {"effort": "low"}},
    )


@pytest.mark.parametrize("model_id", ["openai.gpt-oss-20b-1:0", "openai.gpt-oss-120b-1:0"])
def test_oss_models_are_rejected_before_contacting_bedrock(model_factory, model_id):
    with pytest.raises(ValueError, match="GPT-OSS is not supported"):
        loader.load_model(model_id)
    model_factory.assert_not_called()


def test_hosted_gpt_reasoning_effort_is_configurable(model_factory, monkeypatch):
    monkeypatch.setenv("BEDROCK_REASONING_EFFORT", "medium")
    loader.load_model()
    assert model_factory.call_args.kwargs["additional_request_fields"] == {"reasoning": {"effort": "medium"}}


def test_anthropic_rollback_omits_openai_fields_and_honors_explicit_overrides(model_factory, monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "us.openai.gpt-6-luna")
    monkeypatch.setenv("BEDROCK_MAX_TOKENS", "4096")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    model_id = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    loader.load_model(model_id, "us-east-1")
    model_factory.assert_called_once_with(model_id=model_id, region_name="us-east-1", max_tokens=4096)


@pytest.mark.parametrize("budget", ["0", "255", "16001", "not-a-number"])
def test_invalid_output_budget_fails_before_contacting_bedrock(model_factory, monkeypatch, budget):
    monkeypatch.setenv("BEDROCK_MAX_TOKENS", budget)
    with pytest.raises(ValueError):
        loader.load_model()
    model_factory.assert_not_called()
