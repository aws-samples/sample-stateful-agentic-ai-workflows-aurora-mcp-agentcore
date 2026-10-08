#!/usr/bin/env python3
"""Mint both seeded users' tokens and write them to standard output as one JSON object.

The output is for a pipe into scripts/identity_capture/capture.mjs. It is refused when standard
output is a terminal, so a token never reaches the screen or a scrollback. Passwords come from
Secrets Manager inside scripts/cognito_tokens.py and are never printed.

    mint_session.py | node scripts/identity_capture/capture.mjs <base-url> <out-dir> <html>

Exit codes: 0 written, 1 a sign-in failed (one redacted line on standard error), 3 refused.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

MERIDIAN_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MERIDIAN_DIR))

from dotenv import load_dotenv  # noqa: E402

from scripts.cognito_tokens import mint_tokens  # noqa: E402
from scripts.provision_service_logins import redact  # noqa: E402

USERS = ("jordan", "decoy")


def main(
    *,
    mint: Callable[[str], dict] = mint_tokens,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    """Write ``{"jordan": {"access", "id"}, "decoy": {...}}`` to a pipe."""
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    if out.isatty():
        err.write("Refusing to print tokens to a terminal; pipe this command into capture.mjs.\n")
        return 3
    try:
        tokens = {user: mint(user) for user in USERS}
    except RuntimeError as exc:
        err.write(redact(str(exc)) + "\n")
        return 1
    out.write(json.dumps(tokens))
    out.flush()
    return 0


if __name__ == "__main__":
    load_dotenv(MERIDIAN_DIR / ".env")
    sys.exit(main())
