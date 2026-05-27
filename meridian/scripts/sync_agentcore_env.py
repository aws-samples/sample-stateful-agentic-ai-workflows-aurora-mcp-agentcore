#!/usr/bin/env python3
"""
Sync AgentCore resource IDs from the @aws/agentcore CLI into meridian/.env.

Preferred workflow (Node-based CLI):

    npm install -g @aws/agentcore
    cd meridian/meridian_agentcore
    agentcore add memory --name meridian-session --strategies SEMANTIC
    agentcore add gateway --name meridian-aurora --authorizer-type AWS_IAM
    agentcore add gateway-target --name AuroraSearch --type mcp-server ...
    agentcore deploy -y
    python ../scripts/sync_agentcore_env.py --write

This script reads ``agentcore/.cli/deployed-state.json`` and/or runs
``agentcore status --json``, then prints (or writes) AGENTCORE_* lines.

AWS docs:
  - AgentCore CLI get started:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-cli.html
  - AgentCore dev guide:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/

Usage:
    python scripts/sync_agentcore_env.py
    python scripts/sync_agentcore_env.py --write
    python scripts/sync_agentcore_env.py --fetch-gateway-token
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Allow running from meridian/ root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from backend.agentcore.cli_config import (
    agentcore_project_dir,
    resolve_agentcore_config,
)

load_dotenv()


def _fetch_gateway_access(name: str, project_dir: Path) -> dict | None:
    try:
        result = subprocess.run(
            [
                "agentcore",
                "fetch",
                "access",
                "--name",
                name,
                "--type",
                "gateway",
                "--json",
            ],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Append/update AGENTCORE_* keys in meridian/.env",
    )
    parser.add_argument(
        "--fetch-gateway-token",
        action="store_true",
        help="Run agentcore fetch access for the configured gateway",
    )
    args = parser.parse_args()

    resolve_agentcore_config.cache_clear()
    cfg = resolve_agentcore_config()
    project_dir = agentcore_project_dir()

    lines: dict[str, str] = {
        "AGENTCORE_REGION": cfg.region,
    }
    if cfg.runtime_arn:
        lines["AGENTCORE_RUNTIME_ARN"] = cfg.runtime_arn
    if cfg.runtime_name:
        lines["AGENTCORE_RUNTIME_NAME"] = cfg.runtime_name
    if cfg.gateway_url:
        lines["AGENTCORE_GATEWAY_URL"] = cfg.gateway_url
    if cfg.gateway_name:
        lines["AGENTCORE_GATEWAY_NAME"] = cfg.gateway_name
    if cfg.gateway_search_tool:
        lines["AGENTCORE_GATEWAY_SEARCH_TOOL"] = cfg.gateway_search_tool
    if cfg.memory_id:
        lines["AGENTCORE_MEMORY_ID"] = cfg.memory_id
    if cfg.memory_name:
        lines["AGENTCORE_MEMORY_NAME"] = cfg.memory_name
    if cfg.workload_identity:
        lines["AGENTCORE_WORKLOAD_IDENTITY"] = cfg.workload_identity
    if cfg.resource_provider:
        lines["AGENTCORE_RESOURCE_PROVIDER"] = cfg.resource_provider

    if args.fetch_gateway_token and cfg.gateway_name:
        access = _fetch_gateway_access(cfg.gateway_name, project_dir)
        if access:
            token = access.get("accessToken") or access.get("token")
            if token:
                lines["AGENTCORE_GATEWAY_ACCESS_TOKEN"] = token

    print(f"# AgentCore CLI sync — sources: {', '.join(cfg.sources)}")
    print(f"# Project dir: {cfg.cli_project_dir}")
    for key, value in lines.items():
        print(f"{key}={value}")

    if not cfg.configured_any:
        print("\n# No deployed AgentCore resources found.", file=sys.stderr)
        print("# Run: cd meridian/meridian_agentcore && agentcore deploy -y", file=sys.stderr)
        return 1

    if args.write:
        env_path = Path(__file__).resolve().parents[1] / ".env"
        existing = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
        for key, value in lines.items():
            line = f"{key}={value}"
            if f"{key}=" in existing:
                import re

                existing = re.sub(rf"^{key}=.*$", line, existing, flags=re.MULTILINE)
            else:
                existing = existing.rstrip() + f"\n{line}\n"
        env_path.write_text(existing, encoding="utf-8")
        print(f"\nWrote {len(lines)} keys to {env_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
