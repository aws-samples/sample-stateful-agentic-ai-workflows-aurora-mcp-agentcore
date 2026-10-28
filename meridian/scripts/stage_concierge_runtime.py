"""Copy the concierge definition into the MeridianConcierge Runtime bundle.

AgentCore's CodeZip build zips only the runtime's code location, and the Runtime cannot import
``backend``. backend/agents/concierge_definition.py is therefore copied, byte for byte, into
meridian_agentcore/app/MeridianConcierge/ before every deploy. The copy is generated and
gitignored. scripts/render_agentcore_config.py stages it on every render, and --check reports a
missing or stale copy.
"""

import argparse
import ast
import hashlib
import shutil
import sys
from pathlib import Path
from typing import List, Optional

MERIDIAN = Path(__file__).resolve().parents[1]
SOURCE = MERIDIAN / "backend" / "agents" / "concierge_definition.py"
RUNTIME_DIR = MERIDIAN / "meridian_agentcore" / "app" / "MeridianConcierge"
TARGET_NAME = "concierge_definition.py"


class StageError(RuntimeError):
    """The definition cannot be staged as it stands."""


def _unusable_import(path: Path) -> Optional[str]:
    """Describe the first import the Runtime bundle cannot resolve, or return None."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import) and any(
            alias.name.split(".")[0] == "backend" for alias in node.names
        ):
            return "imports backend"
        if isinstance(node, ast.ImportFrom):
            if node.level > 0:
                return "uses a relative import"
            if (node.module or "").split(".")[0] == "backend":
                return "imports backend"
    return None


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage(target_dir: Path = RUNTIME_DIR) -> Path:
    """Copy the definition into ``target_dir`` and return the copy's path.

    Raises:
        StageError: The source imports ``backend`` or uses a relative import, neither of
            which resolves in the Runtime bundle.
    """
    problem = _unusable_import(SOURCE)
    if problem:
        raise StageError(
            f"{SOURCE} {problem}, which the Runtime cannot resolve; "
            "keep the definition to the standard library and strands, with absolute imports"
        )
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / TARGET_NAME
    shutil.copy2(SOURCE, destination)
    return destination


def check(target_dir: Path = RUNTIME_DIR) -> List[str]:
    """Return the staged files that are missing or differ from the source."""
    destination = target_dir / TARGET_NAME
    if not destination.is_file():
        return [f"{TARGET_NAME} (missing)"]
    return [] if _digest(destination) == _digest(SOURCE) else [TARGET_NAME]


def main(argv: Optional[List[str]] = None) -> int:
    """Stage the copy, or with --check report whether it is stale."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if the copy is stale")
    args = parser.parse_args(argv)
    if args.check:
        stale = check()
        for path in stale:
            print(f"stale: {path}")
        return 1 if stale else 0
    stage()
    print(f"staged {TARGET_NAME} into {RUNTIME_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
