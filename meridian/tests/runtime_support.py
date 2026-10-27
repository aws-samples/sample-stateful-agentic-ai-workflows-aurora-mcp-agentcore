"""Loads the Concierge Runtime's real ``main.py`` in a backend test, with its SDK stubbed.

The Runtime's own SDK (``bedrock_agentcore``) is not installed in the backend venv, so the
imports ``main.py`` makes from it are stubbed. Everything else it runs is the real code.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

RUNTIME = Path(__file__).resolve().parents[1] / "meridian_agentcore" / "app" / "MeridianConcierge"
sys.path.insert(0, str(RUNTIME))

SDK_MODULES = (
    "bedrock_agentcore",
    "bedrock_agentcore.memory",
    "bedrock_agentcore.memory.integrations",
    "bedrock_agentcore.memory.integrations.strands",
    "bedrock_agentcore.memory.integrations.strands.config",
    "bedrock_agentcore.memory.integrations.strands.session_manager",
    "bedrock_agentcore.runtime",
)


class FakeApp:
    def entrypoint(self, handler):
        return handler


def load_runtime_main(monkeypatch, module_name: str):
    """The Runtime's real ``main.py``, with only its AgentCore SDK imports stubbed."""
    for name in SDK_MODULES:
        monkeypatch.setitem(sys.modules, name, ModuleType(name))
    config = sys.modules["bedrock_agentcore.memory.integrations.strands.config"]
    config.AgentCoreMemoryConfig = config.RetrievalConfig = object
    manager = sys.modules["bedrock_agentcore.memory.integrations.strands.session_manager"]
    manager.AgentCoreMemorySessionManager = object
    sys.modules["bedrock_agentcore.runtime"].BedrockAgentCoreApp = FakeApp
    monkeypatch.setenv("AGENTCORE_GATEWAY_MERIDIAN_AURORA_URL", "https://gw-1.gateway.example/mcp")
    monkeypatch.setenv("MEMORY_MERIDIAN_SESSION_ID", "mem-1")
    spec = importlib.util.spec_from_file_location(module_name, RUNTIME / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
