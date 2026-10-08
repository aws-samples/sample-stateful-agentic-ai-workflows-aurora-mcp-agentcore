#!/usr/bin/env python3
"""Run the signed-in capture with the tokens flowing only over a private pipe.

    venv/bin/python scripts/identity_capture/capture_session.py <base-url> <out-dir> <html>
    venv/bin/python scripts/identity_capture/capture_session.py --check

This process creates the pipe. The minter (mint_session.py) inherits the write end and the browser
script (capture.mjs) inherits the read end, each as ``--token-fd N``. Node cannot make a true pipe
(its child pipes are socket pairs), which is why this launcher is Python. The tokens never reach
standard output, standard error, argv, the environment or a file. ``--check`` runs the minter in its
check mode only: it validates the settings and the pipe, mints nothing and starts no browser.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

HERE = Path(__file__).resolve().parent
NODE = "/Users/shayons/.nvm/versions/node/v22.20.0/bin/node"


def default_minter_cmd() -> list[str]:
    """The command that mints both users and writes them to the pipe."""
    return [sys.executable, str(HERE / "mint_session.py")]


def default_reader_cmd() -> list[str]:
    """The command that reads the pipe and drives the browser."""
    return [NODE, str(HERE / "capture.mjs")]


def _first_failure(codes: Sequence[int]) -> int:
    return next((code for code in codes if code != 0), 0)


def run(
    capture_args: Sequence[str],
    *,
    minter_cmd: Sequence[str] | None = None,
    reader_cmd: Sequence[str] | None = None,
) -> int:
    """Start the minter and the reader on one private pipe and return the first failure."""
    minter_cmd = list(minter_cmd or default_minter_cmd())
    reader_cmd = list(reader_cmd or default_reader_cmd())
    read_end, write_end = os.pipe()
    try:
        minter = subprocess.Popen(
            [*minter_cmd, "--token-fd", str(write_end)], pass_fds=(write_end,))
        os.close(write_end)
        reader = subprocess.Popen(
            [*reader_cmd, "--token-fd", str(read_end), *capture_args], pass_fds=(read_end,))
    finally:
        for fd in (write_end, read_end):
            try:
                os.close(fd)
            except OSError:
                pass
    return _first_failure([minter.wait(), reader.wait()])


def check(*, minter_cmd: Sequence[str] | None = None) -> int:
    """Run the minter in check mode against a throwaway pipe; nothing is minted."""
    minter_cmd = list(minter_cmd or default_minter_cmd())
    read_end, write_end = os.pipe()
    try:
        return subprocess.run(
            [*minter_cmd, "--check", "--token-fd", str(write_end)], pass_fds=(write_end,),
        ).returncode
    finally:
        os.close(read_end)
        os.close(write_end)


def main(
    argv: Sequence[str] | None = None,
    *,
    minter_cmd: Sequence[str] | None = None,
    reader_cmd: Sequence[str] | None = None,
) -> int:
    """Run the capture, or only the plumbing check when the one argument is ``--check``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--check"]:
        return check(minter_cmd=minter_cmd)
    return run(args, minter_cmd=minter_cmd, reader_cmd=reader_cmd)


if __name__ == "__main__":
    sys.exit(main())
