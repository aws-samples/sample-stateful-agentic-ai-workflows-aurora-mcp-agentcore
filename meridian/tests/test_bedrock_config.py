"""Unit tests for ``backend.config.BedrockConfig``."""

from __future__ import annotations

import importlib

import pytest


def _reload_config_module():
    """Reload backend.config so its dataclass field default_factories re-read env."""
    import backend.config as cfg_module
    importlib.reload(cfg_module)
    return cfg_module


class TestBedrockConfig:
    def test_default_model_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
        monkeypatch.delenv("BEDROCK_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        cfg = _reload_config_module()
        assert cfg.config.bedrock.model_id == cfg.BedrockConfig.DEFAULT_MODEL_ID
        assert cfg.BedrockConfig.DEFAULT_MODEL_ID == "global.anthropic.claude-sonnet-5"
        # Region falls back to us-east-1 when neither var is set.
        assert cfg.config.bedrock.region == "us-east-1"

    def test_bedrock_model_id_env_overrides_default(self, monkeypatch):
        monkeypatch.setenv("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-4-5-20250929-v1:0")
        monkeypatch.delenv("BEDROCK_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        cfg = _reload_config_module()
        assert cfg.config.bedrock.model_id == "global.anthropic.claude-sonnet-4-5-20250929-v1:0"

    def test_bedrock_region_takes_precedence_over_aws_default_region(self, monkeypatch):
        monkeypatch.setenv("BEDROCK_REGION", "us-west-2")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
        cfg = _reload_config_module()
        assert cfg.config.bedrock.region == "us-west-2"

    def test_aws_default_region_used_when_bedrock_region_unset(self, monkeypatch):
        monkeypatch.delenv("BEDROCK_REGION", raising=False)
        monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
        cfg = _reload_config_module()
        assert cfg.config.bedrock.region == "eu-west-1"

    @pytest.fixture(autouse=True)
    def _restore_module(self):
        """Reload the module a final time at the end of each test so other
        tests don't see leftover monkeypatched env state baked into the
        dataclass default_factory."""
        yield
        importlib.reload(__import__("backend.config", fromlist=["config"]))
