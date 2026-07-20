"""Tests for AgentCore CLI config resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agentcore import cli_config
from backend.agentcore.cli_config import require_agentcore_platform
from backend.agentcore.errors import AgentCoreNotConfiguredError


@pytest.fixture(autouse=True)
def _clear_config_cache():
    cli_config.resolve_agentcore_config.cache_clear()
    yield
    cli_config.resolve_agentcore_config.cache_clear()


def test_parse_deployed_state_runtimes_gateways_memories(tmp_path: Path):
    state = {
        "targets": {
            "default": {
                "region": "us-east-1",
                "resources": {
                    "runtimes": {
                        "MeridianConcierge": {
                            "runtimeArn": "arn:aws:bedrock-agentcore:us-east-1:123:runtime/x",
                            "name": "MeridianConcierge",
                        }
                    },
                    "gateways": {
                        "meridian-aurora": {
                            "gatewayUrl": "https://gw.example.com/mcp",
                            "name": "meridian-aurora",
                        }
                    },
                    "memories": {
                        "meridian_session": {"memoryId": "mem-abc", "name": "meridian_session"}
                    },
                },
            }
        }
    }
    parsed = cli_config._parse_deployed_state(state)
    assert parsed["runtime_arn"].endswith(":runtime/x")
    assert parsed["gateway_url"] == "https://gw.example.com/mcp"
    assert parsed["memory_id"] == "mem-abc"


def test_resolve_from_deployed_state_file(tmp_path: Path, monkeypatch):
    cli_dir = tmp_path / "agentcore" / ".cli"
    cli_dir.mkdir(parents=True)
    (cli_dir / "deployed-state.json").write_text(
        json.dumps(
            {
                "targets": {
                    "default": {
                        "resources": {
                            "runtimes": {
                                "r1": {"runtimeArn": "arn:runtime:1"}
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTCORE_PROJECT_DIR", str(tmp_path / "agentcore"))
    monkeypatch.setenv("AGENTCORE_SKIP_CLI_SYNC", "1")
    monkeypatch.delenv("AGENTCORE_RUNTIME_ARN", raising=False)

    cfg = cli_config.resolve_agentcore_config()
    assert cfg.runtime_arn == "arn:runtime:1"
    assert "deployed-state.json" in cfg.sources


def test_env_overrides_deployed_state(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AGENTCORE_RUNTIME_ARN", "arn:override")
    monkeypatch.setenv("AGENTCORE_SKIP_CLI_SYNC", "1")
    cfg = cli_config.resolve_agentcore_config()
    assert cfg.runtime_arn == "arn:override"
    assert "env" in cfg.sources


def test_require_agentcore_platform_missing(tmp_path, monkeypatch):
    # Point at an empty project dir so no real deployed-state.json is found —
    # otherwise a developer's local `agentcore deploy` would configure the path
    # this test asserts is unconfigured.
    monkeypatch.setenv("AGENTCORE_PROJECT_DIR", str(tmp_path / "agentcore"))
    monkeypatch.setenv("AGENTCORE_SKIP_CLI_SYNC", "1")
    monkeypatch.delenv("AGENTCORE_RUNTIME_ARN", raising=False)
    monkeypatch.delenv("AGENTCORE_GATEWAY_URL", raising=False)
    monkeypatch.delenv("AGENTCORE_MEMORY_ID", raising=False)

    with pytest.raises(AgentCoreNotConfiguredError) as exc:
        require_agentcore_platform()
    assert "runtime_arn" in str(exc.value)
    assert "gateway_url" in str(exc.value)
    assert "memory_id" in str(exc.value)


def test_require_agentcore_platform_ok(monkeypatch):
    monkeypatch.setenv("AGENTCORE_SKIP_CLI_SYNC", "1")
    monkeypatch.setenv("AGENTCORE_RUNTIME_ARN", "arn:runtime:1")
    monkeypatch.setenv("AGENTCORE_GATEWAY_URL", "https://gw.example.com/mcp")
    monkeypatch.setenv("AGENTCORE_MEMORY_ID", "mem-abc")

    cfg = require_agentcore_platform()
    assert cfg.runtime_arn == "arn:runtime:1"
    assert cfg.gateway_url == "https://gw.example.com/mcp"
    assert cfg.memory_id == "mem-abc"
