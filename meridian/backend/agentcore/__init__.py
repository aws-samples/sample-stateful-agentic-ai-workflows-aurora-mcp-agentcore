"""
Bedrock AgentCore adapters for Phase 4 (Production mode).

Provision and deploy with the **Node-based @aws/agentcore CLI** (preferred):

    npm install -g @aws/agentcore
    cd meridian/meridian_agentcore && agentcore deploy -y

Resource ARNs/URLs resolve via ``backend/agentcore/cli_config.py`` from
``meridian_agentcore/agentcore/.cli/deployed-state.json`` or
``agentcore status --json``.

Phase 4 platform story:
  Runtime  — session-isolated agent hosting; the agent owns the tool loop and
             its AgentCore Memory session (see meridian_agentcore/app/MeridianConcierge)
  Gateway  — managed MCP (tools/list + tools/call) with Cedar policy in ENFORCE mode
  Identity — workload identity + resource credentials

The backend keeps three adapters: the runtime client that streams the turn, the
gateway client used only for laptop-side checks, and the identity envelope.

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
from backend.agentcore.runtime import get_agentcore_runtime

__all__ = [
    "agentcore_project_dir",
    "deployed_state_path",
    "resolve_agentcore_config",
    "get_agentcore_gateway",
    "get_agentcore_identity",
    "get_agentcore_runtime",
]
