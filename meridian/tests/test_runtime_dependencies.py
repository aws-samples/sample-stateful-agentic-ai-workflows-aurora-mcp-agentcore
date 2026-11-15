"""The runtime's dependencies resolve to the same versions on every deploy.

The AgentCore CLI packages a CodeZip runtime with ``uv pip install -r pyproject.toml``. That
command ignores ``uv.lock`` but honors ``[tool.uv] constraint-dependencies`` and
``override-dependencies``, so direct dependencies are pinned with ``==``, every transitive package
is pinned as a constraint, and an override replaces a bound a dependency declares.
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


@runtimes
def test_runtime_pins_include_the_mcp_and_certificate_security_fixes(pyproject):
    from packaging.version import Version

    project = _project(pyproject)
    requirements = project["project"]["dependencies"] + project["tool"]["uv"][
        "constraint-dependencies"]
    pins = {_name(requirement): requirement.split("==")[1] for requirement in requirements}
    mcp = Version(pins["mcp"])
    assert mcp >= Version("1.30.0"), "CVE-2026-59951"
    assert not Version("2.0.0") <= mcp < Version("2.2.0"), "CVE-2026-59951, GHSA-5h93-6whr-6q8j"
    assert Version(pins["certifi"]) >= Version("2026.10.19"), "PYSEC-2026-4320"


@runtimes
def test_opentelemetry_is_one_matched_release_set_until_the_distro_pins_it(pyproject):
    # The 0.NNbN contrib packages are pre-releases, allowed only at the exact versions a stable
    # parent pins. The owner's decision of 15 Nov 2026: opentelemetry-api 1.45.2 fixes
    # GHSA-mf5v-w3w3-f6cg (CVE-2026-126360) and distro 0.22.0 still pins 1.45.1 / 0.66b1, so the
    # whole set moves to the matched patch release 1.45.2 / 0.66b2 (stable opentelemetry-sdk
    # 1.45.2 pins that line) and each moved package is overridden. When the distro moves, this
    # test fails: take its pins and drop the OpenTelemetry overrides.
    project = _project(pyproject)
    requirements = project["project"]["dependencies"] + project["tool"]["uv"][
        "constraint-dependencies"]
    independent = {"opentelemetry-propagator-aws-xray", "opentelemetry-sdk-extension-aws"}
    otel = [r for r in requirements
            if _name(r).startswith("opentelemetry") and _name(r) not in independent]
    assert otel and all(r.split("==")[1] in ("1.45.2", "0.66b2") for r in otel), otel
    overrides = [o for o in project["tool"]["uv"].get("override-dependencies", [])
                 if _name(o).startswith("opentelemetry")]
    assert sorted(overrides) == sorted(set(otel))
    assert "aws-opentelemetry-distro==0.22.0" in project["project"]["dependencies"]


@runtimes
def test_an_override_installs_the_version_the_manifest_pins(pyproject):
    project = _project(pyproject)
    requirements = project["project"]["dependencies"] + project["tool"]["uv"][
        "constraint-dependencies"]
    pins = {_name(requirement): requirement for requirement in requirements}
    for override in project["tool"]["uv"].get("override-dependencies", []):
        assert EXACT.match(override), override
        assert pins.get(_name(override)) == override, override
