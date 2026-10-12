#!/usr/bin/env python3
"""Show the current hosted URL from the non-secret local release record.

Run after publish.py --apply. Optionally pass MERIDIAN_HOSTED_AUTH as a JSON
object with username and password for an authenticated reachability check.
Credentials are neither printed, copied to the clipboard nor written to disk.
A reachable URL alone does not prove source parity or a successful live demo.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import urlparse

RECORD = Path(__file__).resolve().parents[1] / ".local" / "hosted-release.json"
TIMEOUT_SECONDS = 15


def load_record() -> dict:
    if not RECORD.exists():
        raise SystemExit(f"No release record at {RECORD}. Follow docs/OPERATIONS.md to publish first.")
    return json.loads(RECORD.read_text())


def release_url(record: dict) -> str:
    url = record.get("site", {}).get("SiteUrl", "")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Release record must contain a credential-free HTTPS site.SiteUrl")
    return url


def reachability(url: str) -> str:
    headers = {}
    credentials = os.environ.get("MERIDIAN_HOSTED_AUTH")
    if credentials:
        try:
            auth = json.loads(credentials)
            token = base64.b64encode(f"{auth['username']}:{auth['password']}".encode()).decode()
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError("MERIDIAN_HOSTED_AUTH must contain username and password") from error
        headers["Authorization"] = f"Basic {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            code = response.status
    except urllib.error.HTTPError as error:
        code = error.code
    except (urllib.error.URLError, TimeoutError):
        return "unreachable"
    if code == 401 and not credentials:
        return "access protected (authenticated validation still required)"
    return "reachable (HTTP 200)" if code == 200 else f"answered HTTP {code}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--open", action="store_true", help="open the address in a browser")
    parser.add_argument("--url", action="store_true", help="print only the address")
    parser.add_argument("--skip-check", action="store_true", help="do not call the site")
    args = parser.parse_args()
    record = load_record()
    try:
        url = release_url(record)
        if args.url:
            print(url)
        else:
            print(f"Meridian   {url}")
            print(f"Release    {record.get('status', 'unknown')}")
            if not args.skip_check:
                print(f"Status     {reachability(url)}")
    except ValueError as error:
        raise SystemExit(str(error)) from None
    if args.open:
        webbrowser.open(url)


if __name__ == "__main__":
    main()
