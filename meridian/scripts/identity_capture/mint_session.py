#!/usr/bin/env python3
"""Mint both seeded users' tokens and send them down an inherited pipe, and nowhere else.

The tokens never reach standard output, standard error, argv, the environment or a file. The
launcher (scripts/identity_capture/capture_session.py) creates a pipe and passes the write end
to this process as an inherited descriptor; the read end stays with the browser script.

    mint_session.py --token-fd N     mint, write one JSON message to descriptor N, close it
    mint_session.py --check --token-fd N
                                     validate the settings and the descriptor; mint nothing

Without --token-fd it refuses. It also refuses a descriptor that is 0, 1 or 2, a terminal, or
anything that is not a pipe. Exit codes: 0 done, 1 a sign-in or the write failed (one redacted
line on standard error), 3 refused.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NoReturn

MERIDIAN_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(MERIDIAN_DIR))

from dotenv import load_dotenv  # noqa: E402

from scripts.cognito_tokens import mint_tokens  # noqa: E402
from scripts.provision_service_logins import redact  # noqa: E402

USERS = ("jordan", "decoy")
REQUIRED_SETTINGS = ("MERIDIAN_COGNITO_USER_POOL_ID", "MERIDIAN_COGNITO_APP_CLIENT_ID")
FIRST_INHERITED_FD = 3


class Refused(Exception):
    """The request is refused before anything is minted."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise Refused(message)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description=__doc__.split("\n\n")[0], add_help=True)
    parser.add_argument("--token-fd", type=int, help="inherited pipe write end (3 or higher)")
    parser.add_argument("--check", action="store_true", help="validate only; mint nothing")
    return parser


def validate_fd(fd: int | None) -> int:
    """Return ``fd`` when it is an open pipe other than 0, 1 and 2, else raise ``Refused``."""
    if fd is None:
        raise Refused("--token-fd is required; run this through capture_session.py")
    if fd < FIRST_INHERITED_FD:
        raise Refused(f"--token-fd must be {FIRST_INHERITED_FD} or higher, not {fd}")
    try:
        mode = os.fstat(fd).st_mode
        is_terminal = os.isatty(fd)
    except OSError:
        raise Refused(f"descriptor {fd} is not open") from None
    if is_terminal:
        raise Refused(f"descriptor {fd} is a terminal; tokens go only into a pipe")
    if not stat.S_ISFIFO(mode):
        raise Refused(f"descriptor {fd} is not a pipe; tokens go only into a pipe")
    return fd


def _missing_settings() -> list[str]:
    return [name for name in REQUIRED_SETTINGS if not os.environ.get(name)]


def _send(fd: int, message: dict) -> None:
    data = json.dumps(message).encode()
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def main(argv: Sequence[str] | None = None, *, mint: Callable[[str], dict] = mint_tokens) -> int:
    """Validate the descriptor, then mint both users and write one JSON message to it."""
    try:
        args = _parser().parse_args(argv)
        fd = validate_fd(args.token_fd)
    except Refused as exc:
        sys.stderr.write(f"refused: {exc}\n")
        return 3
    missing = _missing_settings()
    if missing:
        sys.stderr.write(f"missing settings: {', '.join(missing)}; add them to meridian/.env\n")
        return 1
    if args.check:
        return 0
    try:
        _send(fd, {user: mint(user) for user in USERS})
    except RuntimeError as exc:
        sys.stderr.write(redact(str(exc)) + "\n")
        return 1
    except OSError:
        sys.stderr.write(f"cannot write to descriptor {fd}; is the reader still running?\n")
        return 1
    finally:
        os.close(fd)
    return 0


if __name__ == "__main__":
    load_dotenv(MERIDIAN_DIR / ".env")
    sys.exit(main())
