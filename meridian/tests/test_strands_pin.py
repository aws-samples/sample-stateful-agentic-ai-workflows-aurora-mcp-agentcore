"""The backend runs the same Strands release as the AgentCore runtimes."""

import importlib.metadata
import inspect
import pathlib
import tomllib

from strands.session import SnapshotSessionManager

MERIDIAN = pathlib.Path(__file__).resolve().parents[1]
RUNTIME_PROJECT = MERIDIAN / "meridian_agentcore" / "app" / "MeridianConcierge" / "pyproject.toml"


def test_backend_runs_the_runtime_strands_release():
    runtime = tomllib.loads(RUNTIME_PROJECT.read_text())
    pins = [dep for dep in runtime["project"]["dependencies"] if dep.startswith("strands-agents==")]
    assert pins == ["strands-agents==1.57.2"]
    assert importlib.metadata.version("strands-agents") == "1.57.2"


def test_graph_snapshots_save_after_every_node():
    parameters = inspect.signature(SnapshotSessionManager).parameters
    assert parameters["multi_agent_save_latest_on"].default == "node"
