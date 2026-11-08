#!/usr/bin/env python3
"""Launch the loopback backend with its service login, keeping .env unchanged."""

import argparse
from collections.abc import Mapping
import os
from pathlib import Path
import sys

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def backend_environment(source: Mapping[str, str | None]) -> dict[str, str]:
    """Require explicit service configuration; never fall back to the admin secret."""
    for name in ("AURORA_CLUSTER_ARN", "AURORA_BACKEND_SECRET_ARN"):
        if not (source.get(name) or "").strip():
            raise ValueError(f"Set {name} in meridian/.env before starting the backend.")
    result = {key: value for key, value in source.items() if value is not None}
    result["AURORA_SECRET_ARN"] = result["AURORA_BACKEND_SECRET_ARN"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="check local settings without AWS calls"
    )
    parser.add_argument("--port", type=int, default=8013)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    try:
        environment = backend_environment({**dotenv_values(ROOT / ".env"), **os.environ})
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    if args.check:
        print("Local configuration is present; backend service secret selected. AWS not checked.")
        return 0
    os.environ.update(environment)
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    import uvicorn

    uvicorn.run("backend.main:app", host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
