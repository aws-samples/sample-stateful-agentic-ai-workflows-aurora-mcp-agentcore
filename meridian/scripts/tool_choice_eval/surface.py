"""The tool surface the Concierge's model sees, read from the Gateway's own schema files.

The Gateway serves each ``gateway_targets/<folder>/tool-schema.json`` under the target name
(``meridian_catalog`` becomes ``MeridianCatalog``), so a tool the model sees is named
``MeridianCatalog___search_trips``. This module reads those files, so a changed description or
argument reaches the eval with no second copy to keep in step.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.agents.concierge_definition import WORKFLOW_ONLY_TOOLS, bare_tool_name

TARGETS_DIR = (
    Path(__file__).resolve().parents[2] / "meridian_agentcore" / "agentcore" / "gateway_targets"
)
SEPARATOR = "___"


def target_name(folder: str) -> str:
    """The Gateway target name for a ``gateway_targets`` folder: ``meridian_holds`` is
    ``MeridianHolds``."""
    return "".join(part.capitalize() for part in folder.split("_"))


def gateway_tools(targets_dir: Path = TARGETS_DIR) -> dict[str, dict[str, Any]]:
    """Every tool the Gateway serves, keyed by its Gateway name (``Target___tool``)."""
    tools: dict[str, dict[str, Any]] = {}
    for schema in sorted(targets_dir.glob("*/tool-schema.json")):
        target = target_name(schema.parent.name)
        for tool in json.loads(schema.read_text(encoding="utf-8")):
            tools[f"{target}{SEPARATOR}{tool['name']}"] = tool
    return tools


def model_visible(
    tools: dict[str, dict[str, Any]], spans: set[str] | frozenset[str]
) -> dict[str, dict[str, Any]]:
    """The tools the Runtime hands the model: those it traces, minus the workflow-only ones.

    This is the rule of ``governed_tools`` in the Concierge Runtime's ``main.py``: a tool needs a
    span in ``turn_trace.SPANS``, and a tool in ``WORKFLOW_ONLY_TOOLS`` is withheld.

    Args:
        tools: The Gateway's tools, from ``gateway_tools``.
        spans: The bare tool names the Runtime traces (``turn_trace.SPANS``).
    """
    offered = {}
    for name, tool in tools.items():
        bare = bare_tool_name(name)
        if bare in spans and bare not in WORKFLOW_ONLY_TOOLS:
            offered[name] = tool
    return offered


def strands_spec(gateway_name: str, tool: dict[str, Any]) -> dict[str, Any]:
    """The tool spec Strands sends to the model for an MCP tool: name, description, schema."""
    return {
        "name": gateway_name,
        "description": tool["description"],
        "inputSchema": {"json": tool["inputSchema"]},
    }
