"""AgentCore configuration errors — no silent no-ops.

AWS docs:
  - AgentCore CLI get started:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-get-started-cli.html
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AgentCoreNotConfiguredError(RuntimeError):
    """Raised when Phase 4 requires AgentCore resources that are not deployed."""

    missing: tuple[str, ...]
    project_dir: str
    sources: tuple[str, ...]

    def __str__(self) -> str:
        items = "\n  - ".join(self.missing)
        return (
            "AgentCore platform is not fully configured. Deploy with the CLI:\n"
            "  npm install -g @aws/agentcore\n"
            "  cd meridian/meridian_agentcore && agentcore deploy -y\n"
            "  python scripts/sync_agentcore_env.py --write\n\n"
            f"Missing:\n  - {items}\n"
            f"Config sources: {', '.join(self.sources) or 'none'}\n"
            f"Project dir: {self.project_dir}"
        )
