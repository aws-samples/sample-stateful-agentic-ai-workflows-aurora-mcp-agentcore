#!/usr/bin/env python3
"""
Sync AgentCore resource IDs from the @aws/agentcore CLI into meridian/.env.

Preferred workflow (Node-based CLI):

    cd meridian
    python scripts/render_agentcore_config.py
    # The render needs AURORA_GATEWAY_SECRET_ARN, set by
    # provision_service_logins.py --login gateway --apply
    # --i-understand-this-changes-aws --write-env
    # (migration 018 must be applied first).
    cd meridian_agentcore
    /opt/homebrew/bin/agentcore validate --json
    /opt/homebrew/bin/agentcore deploy -y
    python ../scripts/sync_agentcore_env.py --write

Both runtimes resolve by name from the deployed state: AGENTCORE_RUNTIME_ARN is
MeridianConcierge's and AGENTCORE_WORKFLOW_RUNTIME_ARN is MeridianWorkflow's.

This script reads ``agentcore/.cli/deployed-state.json`` and/or runs
``agentcore status --json``, then prints (or writes) AGENTCORE_* lines.

AWS docs:
  - AgentCore CLI get started:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-cli.html
  - AgentCore dev guide:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/

Printed values are masked (12-digit account ids show as <acct>). Only --write puts the real
values in .env.

Usage:
    python scripts/sync_agentcore_env.py          # print masked lines
    python scripts/sync_agentcore_env.py --write  # write the real values to .env
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Allow running from meridian/ root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

from backend.agentcore.cli_config import resolve_agentcore_config
from backend.security.redaction import mask_account_ids

load_dotenv()


def env_lines(cfg) -> dict[str, str]:
    """The AGENTCORE_* lines for every value the resolved config holds."""
    candidates = {
        "AGENTCORE_REGION": cfg.region,
        "AGENTCORE_RUNTIME_ARN": cfg.runtime_arn,
        "AGENTCORE_WORKFLOW_RUNTIME_ARN": cfg.workflow_runtime_arn,
        "AGENTCORE_RUNTIME_NAME": cfg.runtime_name,
        "AGENTCORE_GATEWAY_URL": cfg.gateway_url,
        "AGENTCORE_GATEWAY_NAME": cfg.gateway_name,
        "AGENTCORE_GATEWAY_SEARCH_TOOL": cfg.gateway_search_tool,
        "AGENTCORE_MEMORY_ID": cfg.memory_id,
        "AGENTCORE_MEMORY_NAME": cfg.memory_name,
    }
    return {key: value for key, value in candidates.items() if value}


def write_env(env_path: Path, lines: dict[str, str]) -> None:
    """Update or append each KEY=value line in the env file."""
    existing = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    for key, value in lines.items():
        line = f"{key}={value}"
        if re.search(rf"^{re.escape(key)}=", existing, re.MULTILINE):
            pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
            existing = pattern.sub(lambda _match, text=line: text, existing)
        else:
            existing = existing.rstrip() + f"\n{line}\n"
    env_path.write_text(existing, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Append/update AGENTCORE_* keys in meridian/.env with the real, unmasked values",
    )
    args = parser.parse_args()

    resolve_agentcore_config.cache_clear()
    cfg = resolve_agentcore_config()
    lines = env_lines(cfg)

    print(mask_account_ids(f"# AgentCore CLI sync - sources: {', '.join(cfg.sources)}"))
    print(mask_account_ids(f"# Project dir: {cfg.cli_project_dir}"))
    print("# Printed values are masked; --write writes the real values to .env.")
    for key, value in lines.items():
        print(mask_account_ids(f"{key}={value}"))

    if not cfg.configured_any:
        print("\n# No deployed AgentCore resources found.", file=sys.stderr)
        print("# Run: cd meridian/meridian_agentcore && agentcore deploy -y", file=sys.stderr)
        return 1

    if args.write:
        env_path = Path(__file__).resolve().parents[1] / ".env"
        write_env(env_path, lines)
        print(f"\nWrote {len(lines)} keys to {env_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
