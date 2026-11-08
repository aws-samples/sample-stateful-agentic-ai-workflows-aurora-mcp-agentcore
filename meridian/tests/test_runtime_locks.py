"""Each AgentCore Runtime has a committed, hashed lock that agrees with its pyproject.

CI installs the lock with ``uv pip install --require-hashes --no-deps`` and recompiles it to catch
drift; this test is the offline half. It fails when an exact pin in a Runtime's pyproject
disagrees with the lock, when a lock entry lacks a hash, and when CI stops reading the lock.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APPS = ROOT / "meridian_agentcore" / "app"
LOCKS = ROOT / "meridian_agentcore" / "runtime-locks"
CI = ROOT.parent / ".github" / "workflows" / "application-ci.yml"
RUNTIMES = ["MeridianConcierge", "MeridianWorkflow"]
runtimes = pytest.mark.parametrize("runtime", RUNTIMES)
ENTRY = re.compile(r"^([A-Za-z0-9][A-Za-z0-9_.\-]*)(\[[^\]]*\])?(.*)$")


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def lock_text(runtime: str) -> str:
    return (LOCKS / f"{runtime}.requirements.lock").read_text(encoding="utf-8")


def lock_entries(text: str) -> list[tuple[str, str, int]]:
    """Return (package, version, hash count) for each requirement in a uv lock."""
    entries: list[tuple[str, str, int]] = []
    for line in text.splitlines():
        if not line or line[0] in "# ":
            if line.strip().startswith("--hash=sha256:") and entries:
                name, version, count = entries[-1]
                entries[-1] = (name, version, count + 1)
            continue
        match = ENTRY.match(line)
        assert match, f"unparseable lock line: {line!r}"
        name, _extras, rest = match.groups()
        version = re.match(r"==([^\s;\\]+)", rest)
        entries.append((normalize(name), version.group(1) if version else "", 0))
    return entries


def locked_versions(runtime: str) -> dict[str, set[str]]:
    versions: dict[str, set[str]] = {}
    for name, version, _count in lock_entries(lock_text(runtime)):
        versions.setdefault(name, set()).add(version)
    return versions


def exact_pins(requirements: list[str]) -> dict[str, str]:
    pins = {}
    for requirement in requirements:
        name, version = requirement.split("==")
        pins[normalize(name.split("[")[0])] = version
    return pins


def pyproject_pins(runtime: str) -> dict[str, dict[str, str]]:
    project = tomllib.loads((APPS / runtime / "pyproject.toml").read_text(encoding="utf-8"))
    uv = project["tool"]["uv"]
    return {
        "dependencies": exact_pins(project["project"]["dependencies"]),
        "override-dependencies": exact_pins(uv["override-dependencies"]),
        "constraint-dependencies": exact_pins(uv["constraint-dependencies"]),
    }


@runtimes
def test_every_lock_entry_is_pinned_and_has_a_hash(runtime):
    entries = lock_entries(lock_text(runtime))

    assert len(entries) > 50
    assert [name for name, version, _ in entries if not version] == []
    assert [name for name, _, hashes in entries if hashes == 0] == []


@runtimes
def test_the_lock_agrees_with_every_exact_pin_in_the_pyproject(runtime):
    locked = locked_versions(runtime)
    problems = []
    for kind, pins in pyproject_pins(runtime).items():
        for name, version in pins.items():
            if name not in locked:
                # A constraint only bounds a package the resolution may not need.
                if kind != "constraint-dependencies":
                    problems.append(f"{kind}: {name}=={version} is missing from the lock")
            elif locked[name] != {version}:
                problems.append(f"{kind}: {name}=={version} but the lock has {sorted(locked[name])}")

    assert problems == []


@runtimes
def test_the_lock_keeps_the_mcp_override(runtime):
    assert locked_versions(runtime)["mcp"] == {pyproject_pins(runtime)["override-dependencies"]["mcp"]}


@runtimes
def test_the_lock_was_compiled_for_the_runtime_python(runtime):
    header = lock_text(runtime).splitlines()[1]
    minor = ".".join((ROOT / ".python-version").read_text().strip().split(".")[:2])

    assert f"--python-version {minor} " in header
    assert "--generate-hashes" in header
    assert "--universal" in header
    assert "--only-binary :all:" in header
    assert f"PYTHON_{minor.replace('.', '_')}" in (
        ROOT / "meridian_agentcore" / "agentcore" / "agentcore.template.json"
    ).read_text()


def test_the_ci_job_installs_the_committed_locks_and_checks_them_for_drift():
    job = CI.read_text().split("  runtime-contracts:")[1].split("\n  frontend:")[0]
    lock = "runtime-locks/${{ matrix.runtime }}.requirements.lock"

    assert f"--no-deps --require-hashes -r meridian_agentcore/{lock}" in job
    assert f"--output-file ../../{lock}" in job
    assert f"git diff --exit-code -- ../../{lock}" in job
    assert "--universal" in job
    assert "pyproject.toml --generate-hashes --output-file" not in job
