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

from importlib import import_module
from typing import Any

# Each public name is imported on first use. The package is imported by every module inside it, and
# the MeridianWorkflow Runtime bundles auth_mode, caller_credential and caller_claims with stdlib
# only, so importing one of them must not load boto3 or the Gateway, Identity and Runtime clients.
_EXPORTS = {
    "agentcore_project_dir": "backend.agentcore.cli_config",
    "deployed_state_path": "backend.agentcore.cli_config",
    "resolve_agentcore_config": "backend.agentcore.cli_config",
    "get_agentcore_gateway": "backend.agentcore.gateway",
    "get_agentcore_identity": "backend.agentcore.identity",
    "get_agentcore_runtime": "backend.agentcore.runtime",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module), name)
    globals()[name] = value
    return value
