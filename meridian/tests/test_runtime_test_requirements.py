"""The SDK-backed Runtime tests run in CI on the same ``bedrock-agentcore`` the Runtime deploys."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "meridian_agentcore" / "app" / "MeridianConcierge"
CI = ROOT.parent / ".github" / "workflows" / "application-ci.yml"
PIN = re.compile(r"^bedrock-agentcore==(\S+?)(?:\s|\\|$)", re.MULTILINE)


def runtime_pin() -> str:
    project = tomllib.loads((RUNTIME / "pyproject.toml").read_text())["project"]
    dependencies = project["dependencies"]
    [pinned] = [name for name in dependencies if name.startswith("bedrock-agentcore==")]
    return pinned.split("==")[1]


def test_the_test_requirements_pin_the_version_the_runtime_deploys():
    [source] = (ROOT / "requirements-runtime-tests.in").read_text().split()
    locked = PIN.findall((ROOT / "requirements-runtime-tests.txt").read_text())

    assert source == f"bedrock-agentcore=={runtime_pin()}"
    assert locked == [runtime_pin()]


def test_the_production_requirements_do_not_ship_the_runtime_sdk():
    assert "bedrock-agentcore" not in (ROOT / "requirements.in").read_text()
    assert not PIN.findall((ROOT / "requirements.txt").read_text())


def test_the_lock_is_hashed_and_ci_installs_and_audits_it():
    text = (ROOT / "requirements-runtime-tests.txt").read_text()
    package_lines = re.findall(r"^[A-Za-z0-9_.-]+==", text, re.MULTILINE)
    hashes = text.count("--hash=sha256:")
    ci = CI.read_text()

    assert hashes >= len(package_lines) > 0
    assert "pip install --require-hashes --no-deps -r requirements-runtime-tests.txt" in ci
    assert "pip_audit -r requirements.txt -r requirements-runtime-tests.txt" in ci


BLOCK = re.compile(r"^([A-Za-z0-9_.-]+)==(\S+?) \\\n((?:    --hash=\S+(?: \\)?\n?)+)", re.MULTILINE)


def locked(text: str) -> dict[str, tuple[str, frozenset[str]]]:
    """Map each package of a hashed lock to its version and its set of hashes."""
    return {
        name: (version, frozenset(re.findall(r"sha256:\w+", hashes)))
        for name, version, hashes in BLOCK.findall(text)
    }


def overridden(main: str, tests: str) -> list[str]:
    """Packages in both locks whose pin or hashes differ, which the second install would change."""
    main_locked = locked(main)
    return sorted(
        name for name, pin in locked(tests).items()
        if name in main_locked and main_locked[name] != pin
    )


def test_the_guard_names_a_package_whose_pin_is_stale_in_the_test_lock():
    main = (ROOT / "requirements.txt").read_text()
    stale = (ROOT / "requirements-runtime-tests.txt").read_text() + (
        "anyio==0.0.1 \\\n    --hash=sha256:00\n"
    )

    assert "anyio" in locked(main)
    assert overridden(main, stale) == ["anyio"]


def test_the_test_lock_cannot_override_a_pin_of_the_main_lock():
    main = (ROOT / "requirements.txt").read_text()
    tests = (ROOT / "requirements-runtime-tests.txt").read_text()

    assert overridden(main, tests) == []
    assert set(locked(tests)) == {"bedrock-agentcore"}
