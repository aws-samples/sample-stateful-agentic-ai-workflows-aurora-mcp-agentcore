"""Audit the exact dependency pins installed into both AgentCore Runtime bundles.

The backend lock does not cover these bundles. Their pyproject constraints pin
the complete dependency sets, so audit them without installing platform wheels.
"""

import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

APPS = Path(__file__).resolve().parents[1] / "meridian_agentcore" / "app"
RUNTIMES = ("MeridianConcierge", "MeridianWorkflow")


def main() -> int:
    failed = False
    with tempfile.TemporaryDirectory(prefix="meridian-runtime-audit-") as directory:
        for name in RUNTIMES:
            project = tomllib.loads((APPS / name / "pyproject.toml").read_text())
            requirements = sorted(set(
                project["project"]["dependencies"]
                + project["tool"]["uv"]["constraint-dependencies"]
            ))
            manifest = Path(directory) / f"{name}.txt"
            manifest.write_text("\n".join(requirements) + "\n")
            print(f"Auditing {name}", flush=True)
            result = subprocess.run([
                sys.executable, "-m", "pip_audit", "--no-deps", "--disable-pip",
                "-r", str(manifest),
            ], check=False)
            failed |= result.returncode != 0
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
