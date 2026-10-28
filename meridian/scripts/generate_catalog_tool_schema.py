#!/usr/bin/env python3
"""Write, or with --check verify, the MeridianCatalog Gateway target's tool-schema.json.

The file is generated from the signatures and docstrings of backend/tools/catalog_tools.py,
so the schema the Gateway publishes cannot drift from the functions Phase 3 calls.

Usage:
    cd meridian
    venv/bin/python scripts/generate_catalog_tool_schema.py [--check]

Exit codes: 0 written or current, 1 stale under --check.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

MERIDIAN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MERIDIAN))

from backend.tools.tool_schema import catalog_schema, render  # noqa: E402

SCHEMA_PATH = (
    MERIDIAN / "meridian_agentcore" / "agentcore" / "gateway_targets" / "meridian_catalog"
    / "tool-schema.json"
)


def main(argv: list[str] | None = None) -> int:
    """Write the schema file, or report whether it is current."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if the file is stale")
    args = parser.parse_args(argv)
    text = render(catalog_schema())
    if args.check:
        current = SCHEMA_PATH.read_text(encoding="utf-8") if SCHEMA_PATH.is_file() else ""
        if current != text:
            print(f"stale: {SCHEMA_PATH}; run scripts/generate_catalog_tool_schema.py")
            return 1
        return 0
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {SCHEMA_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
