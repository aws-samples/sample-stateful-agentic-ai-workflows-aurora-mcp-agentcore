"""
Load AgentCore resource IDs from the @aws/agentcore CLI (preferred).

The Node-based AgentCore CLI (`npm install -g @aws/agentcore`) is the supported
way to provision Runtime, Gateway, Memory, and Identity.  After ``agentcore
deploy``, resource ARNs and URLs land in:

  meridian/meridian_agentcore/agentcore/.cli/deployed-state.json
  (auto-managed — do not edit)

This module merges CLI state with optional ``.env`` overrides so the FastAPI
workshop app and Phase 4 adapters stay in sync with what you deployed.

Resolution order (first match wins per field):

  1. Explicit environment variables (``AGENTCORE_*``)
  2. ``agentcore/.cli/deployed-state.json`` under ``AGENTCORE_PROJECT_DIR``
  3. ``agentcore status --json`` subprocess (when CLI is on PATH)
  4. Unconfigured — Phase 4 raises ``AgentCoreNotConfiguredError``

Docs:
  https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-cli.html
  https://github.com/aws/agentcore-cli
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

# CLI project root (single source of truth):
#   meridian/meridian_agentcore/agentcore
# Override with AGENTCORE_PROJECT_DIR if needed.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PREFERRED_PROJECT_DIR = _PROJECT_ROOT / "meridian_agentcore" / "agentcore"


@dataclass(frozen=True)
class AgentCoreDeployedConfig:
    """Resolved AgentCore platform config for Phase 4."""

    region: str
    runtime_arn: Optional[str] = None
    runtime_name: Optional[str] = None
    runtime_qualifier: str = "DEFAULT"
    gateway_url: Optional[str] = None
    gateway_name: Optional[str] = None
    gateway_search_tool: Optional[str] = None
    memory_id: Optional[str] = None
    memory_name: Optional[str] = None
    workload_identity: Optional[str] = None
    resource_provider: Optional[str] = None
    cli_project_dir: Optional[str] = None
    sources: tuple[str, ...] = field(default_factory=tuple)

    @property
    def configured_any(self) -> bool:
        return any(
            [
                self.runtime_arn,
                self.gateway_url,
                self.memory_id,
                self.workload_identity,
            ]
        )


def agentcore_project_dir() -> Path:
    raw = os.getenv("AGENTCORE_PROJECT_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    return _PREFERRED_PROJECT_DIR


def deployed_state_path() -> Path:
    return agentcore_project_dir() / ".cli" / "deployed-state.json"


def _first_str(*values: Any) -> Optional[str]:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _normalize_gateway_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    url = url.rstrip("/")
    return url if url.endswith("/mcp") else f"{url}/mcp"


def _walk(obj: Any) -> Iterable[Any]:
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item)


def _parse_deployed_state(data: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Best-effort extraction from CLI deployed-state.json (schema evolves)."""
    found: Dict[str, Optional[str]] = {
        "runtime_arn": None,
        "runtime_name": None,
        "gateway_url": None,
        "gateway_name": None,
        "memory_id": None,
        "memory_name": None,
        "workload_identity": None,
        "resource_provider": None,
        "region": None,
    }

    for node in _walk(data):
        if not isinstance(node, dict):
            continue

        found["runtime_arn"] = found["runtime_arn"] or _first_str(
            node.get("runtimeArn"),
            node.get("agentRuntimeArn"),
            node.get("arn") if node.get("resourceType") in ("agent", "runtime") else None,
        )
        found["runtime_name"] = found["runtime_name"] or _first_str(
            node.get("runtimeName"),
            node.get("name") if node.get("runtimeArn") or node.get("agentRuntimeArn") else None,
        )
        found["gateway_url"] = found["gateway_url"] or _first_str(
            node.get("gatewayUrl"),
            node.get("gatewayEndpoint"),
            node.get("url") if node.get("resourceType") == "gateway" else None,
        )
        found["gateway_name"] = found["gateway_name"] or _first_str(
            node.get("gatewayName"),
            node.get("name") if node.get("gatewayUrl") or node.get("gatewayEndpoint") else None,
        )
        found["memory_id"] = found["memory_id"] or _first_str(
            node.get("memoryId"),
            node.get("id") if node.get("resourceType") == "memory" else None,
        )
        found["memory_name"] = found["memory_name"] or _first_str(
            node.get("memoryName"),
            node.get("name") if node.get("memoryId") else None,
        )
        found["workload_identity"] = found["workload_identity"] or _first_str(
            node.get("workloadIdentityArn"),
            node.get("workloadIdentity"),
        )
        found["resource_provider"] = found["resource_provider"] or _first_str(
            node.get("resourceCredentialProviderName"),
            node.get("resourceProvider"),
        )
        found["region"] = found["region"] or _first_str(node.get("region"))

    return found


def _parse_status_json(data: Dict[str, Any]) -> Dict[str, Optional[str]]:
    found: Dict[str, Optional[str]] = {
        "runtime_arn": None,
        "runtime_name": None,
        "gateway_url": None,
        "gateway_name": None,
        "memory_id": None,
        "memory_name": None,
        "workload_identity": None,
        "resource_provider": None,
        "region": None,
    }

    resources = data.get("resources")
    if isinstance(resources, list):
        for resource in resources:
            if not isinstance(resource, dict):
                continue
            rtype = (resource.get("resourceType") or resource.get("type") or "").lower()
            merged = _parse_deployed_state({"resources": [resource]})
            if rtype in ("agent", "runtime"):
                found["runtime_arn"] = found["runtime_arn"] or merged["runtime_arn"]
                found["runtime_name"] = found["runtime_name"] or merged["runtime_name"]
            elif rtype == "gateway":
                found["gateway_url"] = found["gateway_url"] or merged["gateway_url"]
                found["gateway_name"] = found["gateway_name"] or merged["gateway_name"]
            elif rtype == "memory":
                found["memory_id"] = found["memory_id"] or merged["memory_id"]
                found["memory_name"] = found["memory_name"] or merged["memory_name"]
            elif rtype in ("identity", "credential"):
                found["workload_identity"] = found["workload_identity"] or merged["workload_identity"]
                found["resource_provider"] = found["resource_provider"] or merged["resource_provider"]

    for key in found:
        if not found[key]:
            found[key] = _parse_deployed_state(data).get(key)

    return found


def _load_deployed_state_file() -> Optional[Dict[str, Any]]:
    path = deployed_state_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return None


def _run_agentcore_status_json(project_dir: Path) -> Optional[Dict[str, Any]]:
    if os.getenv("AGENTCORE_SKIP_CLI_SYNC", "").lower() in ("1", "true", "yes"):
        return None
    try:
        result = subprocess.run(
            ["agentcore", "status", "--json"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.debug("agentcore status --json unavailable: %s", exc)
        return None

    if result.returncode != 0 or not result.stdout.strip():
        logger.debug("agentcore status failed: %s", (result.stderr or "")[:200])
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


@lru_cache(maxsize=1)
def resolve_agentcore_config() -> AgentCoreDeployedConfig:
    """
    Merge env overrides with CLI-deployed resource metadata.

    Call ``resolve_agentcore_config.cache_clear()`` in tests or after deploy.
    """
    sources: list[str] = []
    project_dir = agentcore_project_dir()
    region = os.getenv(
        "AGENTCORE_REGION",
        os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
    )

    merged: Dict[str, Optional[str]] = {
        "runtime_arn": None,
        "runtime_name": None,
        "gateway_url": None,
        "gateway_name": None,
        "memory_id": None,
        "memory_name": None,
        "workload_identity": None,
        "resource_provider": None,
        "region": region,
    }

    state_data = _load_deployed_state_file()
    if state_data:
        for key, value in _parse_deployed_state(state_data).items():
            if value:
                merged[key] = value
        sources.append("deployed-state.json")

    status_data = _run_agentcore_status_json(project_dir)
    if status_data:
        for key, value in _parse_status_json(status_data).items():
            if value and not merged.get(key):
                merged[key] = value
        sources.append("agentcore status --json")

    # Environment overrides (presenter / CI can pin without redeploying)
    env_map = {
        "runtime_arn": "AGENTCORE_RUNTIME_ARN",
        "runtime_name": "AGENTCORE_RUNTIME_NAME",
        "gateway_url": "AGENTCORE_GATEWAY_URL",
        "gateway_name": "AGENTCORE_GATEWAY_NAME",
        "memory_id": "AGENTCORE_MEMORY_ID",
        "memory_name": "AGENTCORE_MEMORY_NAME",
        "workload_identity": "AGENTCORE_WORKLOAD_IDENTITY",
        "resource_provider": "AGENTCORE_RESOURCE_PROVIDER",
        "region": "AGENTCORE_REGION",
    }
    for field_name, env_key in env_map.items():
        value = os.getenv(env_key)
        if value:
            merged[field_name] = value.strip()
            if "env" not in sources:
                sources.append("env")

    if not sources:
        sources.append("unconfigured")

    return AgentCoreDeployedConfig(
        region=merged["region"] or region,
        runtime_arn=merged["runtime_arn"],
        runtime_name=merged["runtime_name"],
        runtime_qualifier=os.getenv("AGENTCORE_RUNTIME_QUALIFIER", "DEFAULT"),
        gateway_url=_normalize_gateway_url(merged["gateway_url"]),
        gateway_name=merged["gateway_name"],
        gateway_search_tool=os.getenv(
            "AGENTCORE_GATEWAY_SEARCH_TOOL",
            "SemanticTripSearchLambda___semantic_trip_search",
        ),
        memory_id=merged["memory_id"],
        memory_name=merged["memory_name"],
        workload_identity=merged["workload_identity"],
        resource_provider=merged["resource_provider"],
        cli_project_dir=str(project_dir),
        sources=tuple(sources),
    )


def require_agentcore_platform(
    *,
    require_runtime: bool = True,
    require_gateway: bool = True,
    require_memory: bool = True,
) -> AgentCoreDeployedConfig:
    """
    Validate that AgentCore Runtime, Gateway, and Memory are deployed.

    Phase 4 calls real AgentCore data-plane APIs only — no in-process stubs.
    """
    from backend.agentcore.errors import AgentCoreNotConfiguredError

    resolve_agentcore_config.cache_clear()
    cfg = resolve_agentcore_config()
    missing: list[str] = []

    if require_runtime and not cfg.runtime_arn:
        missing.append("runtime_arn (agentcore deploy → AGENTCORE_RUNTIME_ARN)")
    if require_gateway and not cfg.gateway_url:
        missing.append("gateway_url (agentcore add gateway → AGENTCORE_GATEWAY_URL)")
    if require_memory and not cfg.memory_id:
        missing.append("memory_id (agentcore add memory → AGENTCORE_MEMORY_ID)")

    if missing:
        raise AgentCoreNotConfiguredError(
            missing=tuple(missing),
            project_dir=cfg.cli_project_dir or str(agentcore_project_dir()),
            sources=cfg.sources,
        )
    return cfg
