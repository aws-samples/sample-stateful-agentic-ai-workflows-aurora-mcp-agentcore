"""The Runtime carries a byte-identical copy of the definition, and a stale copy is found."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import stage_concierge_runtime as stage

APP = Path(__file__).resolve().parents[1] / "meridian_agentcore" / "app" / "MeridianConcierge"


def test_stage_copies_the_source_byte_for_byte(tmp_path):
    destination = stage.stage(tmp_path)
    assert destination == tmp_path / "concierge_definition.py"
    assert destination.read_bytes() == stage.SOURCE.read_bytes()
    assert stage.check(tmp_path) == []


def test_check_finds_a_copy_that_drifted(tmp_path):
    destination = stage.stage(tmp_path)
    destination.write_text(destination.read_text() + "\n# drift\n")
    assert stage.check(tmp_path) == ["concierge_definition.py"]


def test_check_finds_a_missing_copy(tmp_path):
    assert stage.check(tmp_path) == ["concierge_definition.py (missing)"]


def test_check_finds_a_copy_when_the_source_moved_on(tmp_path, monkeypatch):
    stage.stage(tmp_path)
    newer = tmp_path / "source.py"
    newer.write_text(stage.SOURCE.read_text() + "\nNEWER = 1\n")
    monkeypatch.setattr(stage, "SOURCE", newer)
    assert stage.check(tmp_path) == ["concierge_definition.py"]


@pytest.mark.parametrize("line", [
    "import backend",
    "import backend.config",
    "import json, backend.config as c",
    "from backend import config",
    "from backend.config import config",
])
def test_stage_refuses_a_source_that_imports_backend(tmp_path, monkeypatch, line):
    bad = tmp_path / "source.py"
    bad.write_text(line + "\n")
    monkeypatch.setattr(stage, "SOURCE", bad)
    with pytest.raises(stage.StageError, match="imports backend"):
        stage.stage(tmp_path / "runtime")
    assert not (tmp_path / "runtime" / "concierge_definition.py").exists()


@pytest.mark.parametrize("line", ["from .x import y", "from ..x import y", "from . import y"])
def test_stage_refuses_a_relative_import(tmp_path, monkeypatch, line):
    bad = tmp_path / "source.py"
    bad.write_text(line + "\n")
    monkeypatch.setattr(stage, "SOURCE", bad)
    with pytest.raises(stage.StageError, match="relative import"):
        stage.stage(tmp_path / "runtime")
    assert not (tmp_path / "runtime" / "concierge_definition.py").exists()


@pytest.mark.parametrize("line", ["import backends", "from backend_utils import x", "import json"])
def test_stage_accepts_names_that_only_resemble_backend(tmp_path, monkeypatch, line):
    fine = tmp_path / "source.py"
    fine.write_text(line + "\n")
    monkeypatch.setattr(stage, "SOURCE", fine)
    assert stage.stage(tmp_path / "runtime").is_file()


def test_a_staged_copy_imports_with_only_the_runtimes_own_dependencies(tmp_path):
    stage.stage(tmp_path)
    probe = (
        "import sys\n"
        f"sys.path.insert(0, {str(tmp_path)!r})\n"
        "import concierge_definition as d\n"
        "assert d.PHASE3_TOOLS[0] == 'search_trips'\n"
        "assert not any(m == 'backend' or m.startswith('backend.') for m in sys.modules)\n"
    )
    result = subprocess.run([sys.executable, "-I", "-c", probe], cwd=tmp_path,
                            capture_output=True, text=True,
                            env={"PATH": "/usr/bin:/bin", "AWS_DEFAULT_REGION": "us-east-1"})
    assert result.returncode == 0, result.stderr[-2000:]


def test_the_runtime_directory_ignores_the_generated_copy():
    ignored = (APP / ".gitignore").read_text().splitlines()
    assert "/concierge_definition.py" in ignored


def test_the_copys_only_third_party_import_is_a_direct_runtime_dependency():
    import tomllib

    project = tomllib.loads((APP / "pyproject.toml").read_text())
    direct = {entry.split("[")[0].split("=")[0].lower() for entry in
              project["project"]["dependencies"]}
    tree = ast.parse(stage.SOURCE.read_text())
    roots = {n.module.split(".")[0] for n in ast.walk(tree)
             if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module}
    assert roots - {"__future__", "json", "typing"} == {"strands"}
    assert "strands-agents" in direct
