"""Owner-only files and folders for the harness output (ledger, verdicts, recorded events)."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

FILE_MODE = 0o600
DIR_MODE = 0o700


def private_dir(path: Path) -> None:
    """Create ``path`` and any missing parents with mode 0700; existing folders are untouched."""
    if path.exists():
        return
    private_dir(path.parent)
    path.mkdir(mode=DIR_MODE, exist_ok=True)
    path.chmod(DIR_MODE)


def write_private(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically with mode 0600.

    The text goes to a staging file whose name is unique to this write, created exclusively, then
    flushed and synced, then renamed over ``path``. A failure removes the staging file and leaves
    any earlier content of ``path`` in place.
    """
    private_dir(path.parent)
    staging = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
    try:
        with os.fdopen(descriptor, "w") as handle:
            os.fchmod(handle.fileno(), FILE_MODE)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, path)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
