"""The application does not use LangGraph; only the maintained example does."""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENTRY_MODULES = (
    "backend.main",
    "backend.routers.chat",
    "backend.agents.phase_05_workflow.service",
)
PROBE = (
    "import importlib, sys\n"
    "importlib.import_module(sys.argv[1])\n"
    "loaded = sorted(m for m in sys.modules if m.split('.')[0] == 'langgraph')\n"
    "print('LANGGRAPH_MODULES=' + ','.join(loaded))\n"
)


@pytest.mark.parametrize("module", ENTRY_MODULES)
def test_entry_module_loads_without_langgraph(module):
    result = subprocess.run(
        [sys.executable, "-c", PROBE, module],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    report = result.stdout.strip().splitlines()[-1]
    assert report == "LANGGRAPH_MODULES=", f"{module} loaded langgraph: {report}"


def _langgraph_imports(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.split(".")[0] == "langgraph"]
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "langgraph":
            found.append(node.module)
    return found


def test_no_backend_or_script_file_imports_langgraph():
    offenders = {
        str(path.relative_to(ROOT)): imports
        for folder in ("backend", "scripts")
        for path in (ROOT / folder).rglob("*.py")
        if not {"venv", "site-packages"} & set(path.parts)
        if (imports := _langgraph_imports(path))
    }

    assert offenders == {}
