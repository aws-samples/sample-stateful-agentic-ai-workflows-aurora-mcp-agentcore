"""
Bedrock AgentCore adapters for Phase 4 (Production mode).

Provision and deploy with the **Node-based @aws/agentcore CLI** (preferred):

    npm install -g @aws/agentcore
    cd meridian/meridian_agentcore && agentcore deploy -y

Resource ARNs/URLs resolve via ``backend/agentcore/cli_config.py`` from
``meridian_agentcore/agentcore/.cli/deployed-state.json`` or
``agentcore status --json``.

Phase 4 platform story:
  Runtime  — session-isolated agent hosting
  Gateway  — managed MCP (tools/list + tools/call)
  Memory   — managed session store
  Identity — workload identity + resource credentials

AWS docs:
  - AgentCore overview:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html
  - Runtime / Gateway / Memory / Identity dev guide index:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/
"""

from backend.agentcore.cli_config import (
    agentcore_project_dir,
    deployed_state_path,
    resolve_agentcore_config,
)
from backend.agentcore.gateway import get_agentcore_gateway
from backend.agentcore.identity import get_agentcore_identity
from backend.agentcore.memory import get_agentcore_memory
from backend.agentcore.runtime import get_agentcore_runtime

__all__ = [
    "agentcore_project_dir",
    "deployed_state_path",
    "resolve_agentcore_config",
    "get_agentcore_gateway",
    "get_agentcore_identity",
    "get_agentcore_memory",
    "get_agentcore_runtime",
]
