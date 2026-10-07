"""Workflow code must import without FastAPI, the routers, or LangGraph.

A2 runs the same modules inside AgentCore Runtime, where none of those exist.
Each module is imported in a fresh interpreter so earlier imports cannot hide
a dependency.
"""

import subprocess
import sys
from pathlib import Path

import pytest

MERIDIAN = Path(__file__).resolve().parents[1]
FORBIDDEN = ("fastapi", "backend.routers", "langgraph")
FRAMEWORK_FREE = [
    "backend.activity",
    "backend.agents.phase_05_workflow.hold_intent",
    "backend.agents.phase_05_workflow.governed_hold",
    "backend.agents.phase_05_workflow.packages",
    "backend.retrieval.hybrid",
    "backend.retrieval.availability",
    "backend.agents.phase_05_workflow.memory_recall",
    "backend.agents.phase_05_workflow.routing",
    "backend.agents.phase_05_workflow.snapshot_storage",
    "backend.agents.phase_05_workflow.state",
    "backend.agents.phase_05_workflow.nodes",
]


@pytest.mark.parametrize("module", FRAMEWORK_FREE)
def test_module_imports_without_web_or_langgraph(module):
    probe = (
        f"import sys, {module}\n"
        f"bad = sorted(m for m in sys.modules if m.split('.')[0] in {FORBIDDEN!r}"
        f" or m.startswith('backend.routers'))\n"
        "print(','.join(bad))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], cwd=MERIDIAN, capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "", f"{module} pulls in {result.stdout.strip()}"
