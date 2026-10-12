"""The runtime's dependencies resolve to the same versions on every deploy.

The AgentCore CLI packages a CodeZip runtime with ``uv pip install -r pyproject.toml``. That
command ignores ``uv.lock`` but honors ``[tool.uv] constraint-dependencies``, so direct
dependencies are pinned with ``==`` and every transitive package is pinned as a constraint.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

APPS = Path(__file__).resolve().parents[1] / "meridian_agentcore" / "app"
PYPROJECTS = [
    APPS / "MeridianConcierge" / "pyproject.toml",
    APPS / "MeridianWorkflow" / "pyproject.toml",
]
runtimes = pytest.mark.parametrize("pyproject", PYPROJECTS, ids=lambda p: p.parent.name)
EXACT = re.compile(r"^[A-Za-z0-9_.\-]+(\[[A-Za-z0-9_,\-]+\])?==[0-9][A-Za-z0-9_.!+\-]*$")


def _project(pyproject: Path) -> dict:
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))


def _name(requirement: str) -> str:
    return re.split(r"[\[=<>!~; ]", requirement, maxsplit=1)[0].lower().replace("_", "-")


@runtimes
def test_direct_dependencies_are_pinned_exactly(pyproject):
    unpinned = [d for d in _project(pyproject)["project"]["dependencies"] if not EXACT.match(d)]
    assert unpinned == []


@runtimes
def test_transitive_dependencies_are_pinned_as_constraints(pyproject):
    project = _project(pyproject)
    constraints = project["tool"]["uv"]["constraint-dependencies"]
    assert [c for c in constraints if not EXACT.match(c)] == []
    names = [_name(c) for c in constraints]
    assert len(names) == len(set(names)), "a package is constrained twice"
    assert {"boto3", "opentelemetry-api", "opentelemetry-sdk"} <= set(names)


@runtimes
def test_a_direct_dependency_is_not_constrained_to_a_different_version(pyproject):
    project = _project(pyproject)
    direct = {_name(d): d.split("==")[1] for d in project["project"]["dependencies"]}
    for constraint in project["tool"]["uv"]["constraint-dependencies"]:
        name = _name(constraint)
        if name in direct:
            assert constraint.split("==")[1] == direct[name], name


def test_both_runtimes_pin_the_same_strands_and_agentcore_sdk():
    pins = [{_name(d): d for d in tomllib.loads(p.read_text())["project"]["dependencies"]}
            for p in PYPROJECTS]
    for package in ("strands-agents", "bedrock-agentcore"):
        assert pins[0][package] == pins[1][package]
