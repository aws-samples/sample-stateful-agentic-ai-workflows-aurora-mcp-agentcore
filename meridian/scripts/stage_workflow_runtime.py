"""Copy the workflow's backend modules into the MeridianWorkflow Runtime bundle.

AgentCore's CodeZip build zips only the runtime's code location, so the backend
modules the Phase 5 workflow imports are staged into
meridian_agentcore/app/MeridianWorkflow/backend/ before every deploy. The copy is
generated and gitignored. scripts/render_agentcore_config.py stages it on every
render, and --check reports a stale bundle.
"""

import argparse
import ast
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Set

MERIDIAN = Path(__file__).resolve().parents[1]
RUNTIME_DIR = MERIDIAN / "meridian_agentcore" / "app" / "MeridianWorkflow"
ENTRY_MODULE = "backend.agents.phase_05_workflow.runtime_entry"
MANIFEST = "BUNDLE_MANIFEST.json"


def requirement_name(requirement: str) -> str:
    """The normalized distribution name of a requirement string."""
    return re.split(r"[\[=<>!~; ]", requirement, maxsplit=1)[0].lower().replace("_", "-")


def _module_file(name: str) -> Optional[Path]:
    base = MERIDIAN.joinpath(*name.split("."))
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    return None


def _imports(path: Path) -> Iterable[ast.AST]:
    return (node for node in ast.walk(ast.parse(path.read_text(), filename=str(path)))
            if isinstance(node, (ast.Import, ast.ImportFrom)))


def _backend_names(path: Path) -> Set[str]:
    names: Set[str] = set()
    for node in _imports(path):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names if a.name.split(".")[0] == "backend"}
        elif node.level == 0 and node.module and node.module.split(".")[0] == "backend":
            names.add(node.module)
            names |= {f"{node.module}.{a.name}" for a in node.names}
    return names


def closure(entry: str = ENTRY_MODULE) -> List[Path]:
    """Return every backend module file the entry reaches, parents included."""
    seen: Set[Path] = set()
    queue = [entry]
    while queue:
        parts = queue.pop().split(".")
        for depth in range(1, len(parts) + 1):
            found = _module_file(".".join(parts[:depth]))
            if found and found not in seen:
                seen.add(found)
                queue.extend(_backend_names(found))
    return sorted(seen)


def third_party_imports(entry: str = ENTRY_MODULE) -> Set[str]:
    """Top-level non-stdlib, non-backend modules the closure imports."""
    tops: Set[str] = set()
    for path in closure(entry):
        for node in _imports(path):
            if isinstance(node, ast.Import):
                tops |= {a.name.split(".")[0] for a in node.names}
            elif node.level == 0 and node.module:
                tops.add(node.module.split(".")[0])
    return {t for t in tops if t != "backend" and t not in sys.stdlib_module_names
            and t != "__future__"}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage(target: Path = RUNTIME_DIR) -> Path:
    """Copy the closure into ``target/backend`` and return the manifest path.

    Raises:
        RuntimeError: ``target/backend`` is a real package, which has no bundle manifest.
    """
    bundle = target / "backend"
    if (bundle / "__init__.py").exists() and not (bundle / MANIFEST).exists():
        raise RuntimeError(
            f"{bundle} holds a real backend package, not a staged bundle (no {MANIFEST}); "
            "refusing to delete it. Stage into a Runtime directory instead."
        )
    if bundle.exists():
        shutil.rmtree(bundle)
    manifest = {}
    for source in closure():
        relative = source.relative_to(MERIDIAN).as_posix()
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        manifest[relative] = _digest(source)
    path = bundle / MANIFEST
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return path


def check(target: Path = RUNTIME_DIR) -> List[str]:
    """Return the bundle paths that are missing or differ from the source."""
    manifest_path = target / "backend" / MANIFEST
    staged = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    expected = {p.relative_to(MERIDIAN).as_posix(): _digest(p) for p in closure()}
    stale = {path for path, digest in expected.items()
             if staged.get(path) != digest or not (target / path).is_file()
             or _digest(target / path) != digest}
    return sorted(stale | (set(staged) - set(expected)))


def main() -> int:
    """Stage the bundle, or with --check report whether it is stale."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if the bundle is stale")
    if parser.parse_args().check:
        stale = check()
        for path in stale:
            print(f"stale: {path}")
        return 1 if stale else 0
    print(f"staged {len(json.loads(stage().read_text()))} modules into {RUNTIME_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
