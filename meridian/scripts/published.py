#!/usr/bin/env python3
"""Show where Meridian is published and put the password on the clipboard.

The address and its basic-auth credentials belong to one AWS account, and this
repository is public, so none of it is committed. ``publish.py`` records them in
``.local/published.json``, which is gitignored and owner-readable only. This
reads that record back on presentation day: the address on screen, the password
in the paste buffer, and a reachability check before you walk on.

The password is never printed, so this is safe to run on a shared screen.

Usage:
    cd meridian
    python scripts/published.py           # address, user, reachability, password copied
    python scripts/published.py --open    # also open it in the default browser
    python scripts/published.py --url     # print only the address, for piping
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

RECORD = Path(__file__).resolve().parents[1] / ".local" / "published.json"
CLIPBOARDS = (("pbcopy",), ("wl-copy",), ("xclip", "-selection", "clipboard"))
TIMEOUT_SECONDS = 15


def load_record() -> dict:
    """The publish record, or a clear instruction when there is not one yet."""
    if not RECORD.exists():
        raise SystemExit(
            f"No publish record at {RECORD}.\n"
            "Run `python scripts/publish.py` first; it writes the address and "
            "credentials there and never commits them."
        )
    return json.loads(RECORD.read_text())


def copy_to_clipboard(value: str) -> str | None:
    """Put a value on the clipboard without printing it. Returns the tool used."""
    for command in CLIPBOARDS:
        if shutil.which(command[0]):
            subprocess.run(command, input=value.encode(), check=True)
            return command[0]
    return None


def reachability(url: str, user: str, password: str) -> str:
    """One authenticated request, so a dead deployment is found before the talk."""
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    request = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            code = response.status
    except urllib.error.HTTPError as error:
        code = error.code
    except (urllib.error.URLError, TimeoutError) as error:
        return f"unreachable ({error})"
    return "live" if code == 200 else f"answered {code}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--open", action="store_true", help="open the address in a browser")
    parser.add_argument("--url", action="store_true", help="print only the address")
    parser.add_argument("--skip-check", action="store_true", help="do not call the site")
    args = parser.parse_args()

    record = load_record()
    url, user, password = record.get("url"), record.get("user"), record.get("password")
    if not url:
        raise SystemExit(f"{RECORD} has no url. Re-run `python scripts/publish.py`.")

    if args.url:
        print(url)
    else:
        print(f"Meridian   {url}")
        print(f"User       {user}")
        if not args.skip_check:
            print(f"Status     {reachability(url, user, password)}")
        tool = copy_to_clipboard(password) if password else None
        if tool:
            print(f"Password   copied to the clipboard with {tool}; paste it at the prompt")
        elif password:
            print(f"Password   in {RECORD} under \"password\"; no clipboard tool was found")

    if args.open:
        webbrowser.open(url)


if __name__ == "__main__":
    main()
