"""The Runtime bundle carries every backend module the workflow imports, and nothing web."""

import importlib.metadata
import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from scripts import stage_workflow_runtime as stage

APP = Path(__file__).resolve().parents[1] / "meridian_agentcore" / "app" / "MeridianWorkflow"


def test_the_closure_reaches_lazily_imported_modules():
    names = {p.relative_to(stage.MERIDIAN).as_posix() for p in stage.closure()}
    for lazy in ("backend/agents/phase_05_workflow/service.py", "backend/retrieval/hybrid.py",
                 "backend/retrieval/availability.py", "backend/db/rds_data_client.py",
                 "backend/agentcore/gateway.py", "backend/memory/store.py"):
        assert lazy in names


def test_stage_refuses_to_delete_a_real_backend_package(tmp_path):
    real = tmp_path / "backend"
    real.mkdir()
    (real / "__init__.py").write_text("")
    (real / "keep.py").write_text("x = 1\n")
    with pytest.raises(RuntimeError, match="real backend package"):
        stage.stage(tmp_path)
    assert (real / "keep.py").is_file()
    assert (real / "__init__.py").is_file()


def test_the_closure_has_no_web_layer():
    names = {str(p) for p in stage.closure()}
    assert not [n for n in names if "/routers/" in n or n.endswith("/backend/main.py")]


def test_a_staged_bundle_imports_with_nothing_but_itself_and_site_packages(tmp_path):
    manifest = json.loads(stage.stage(tmp_path).read_text())
    modules = sorted(p[:-3].replace("/", ".").removesuffix(".__init__") for p in manifest
                     if p.endswith(".py"))
    probe = ("import importlib, sys\n"
             f"sys.path.insert(0, {str(tmp_path)!r})\n"
             f"for name in {modules!r}:\n    importlib.import_module(name)\n"
             "assert all(m.__file__.startswith(" + repr(str(tmp_path)) + ")"
             " for n, m in sys.modules.items()"
             " if n.startswith('backend') and getattr(m, '__file__', None))\n")
    result = subprocess.run([sys.executable, "-I", "-c", probe], cwd=tmp_path,
                            capture_output=True, text=True,
                            env={"AGENTCORE_SKIP_CLI_SYNC": "1", "AWS_DEFAULT_REGION": "us-east-1",
                                 "PATH": "/usr/bin:/bin"})
    assert result.returncode == 0, result.stderr[-3000:]


def test_check_finds_a_stale_file(tmp_path):
    stage.stage(tmp_path)
    assert stage.check(tmp_path) == []
    victim = tmp_path / "backend" / "agents" / "phase_05_workflow" / "runtime_entry.py"
    victim.write_text(victim.read_text() + "\n# drift\n")
    assert stage.check(tmp_path) == ["backend/agents/phase_05_workflow/runtime_entry.py"]


def test_every_third_party_import_is_a_direct_runtime_dependency():
    project = tomllib.loads((APP / "pyproject.toml").read_text())
    direct = {stage.requirement_name(d) for d in project["project"]["dependencies"]}
    owners = importlib.metadata.packages_distributions()
    missing = {}
    for module in stage.third_party_imports():
        dists = {d.lower().replace("_", "-") for d in owners.get(module, [module])}
        if not dists & direct:
            missing[module] = sorted(dists)
    assert missing == {}
