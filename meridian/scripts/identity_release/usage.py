"""The command-line parser the release tools share: no abbreviations, usage errors exit 3."""

from __future__ import annotations

import argparse
import re
import sys
from typing import Any, NoReturn

EXIT_USAGE = 3
ACCOUNT_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")


class UsageParser(argparse.ArgumentParser):
    """A parser that refuses abbreviated flags and exits 3, never echoing an account id."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> NoReturn:
        """Print the usage and the masked message to stderr, then exit with ``EXIT_USAGE``."""
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {ACCOUNT_ID.sub('<acct>', message)}", file=sys.stderr)
        sys.exit(EXIT_USAGE)
