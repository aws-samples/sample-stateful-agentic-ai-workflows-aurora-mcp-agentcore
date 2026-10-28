"""A stdio MCP server for the Phase 3 error and start tests; no network, no AWS.

``catalog`` serves the Phase 3 MCP tool names with canned behavior. ``silent`` writes its pid to the
file named by the next argument, then never answers ``initialize``, like a server that hangs
before its handshake.
"""

import os
import sys
import time
from pathlib import Path

LEAK = "Aurora said password=hunter2 for arn:aws:iam::123456789012:role/MeridianCatalog"


BOTO_ERRORS = {
    "AccessDeniedException": "User: arn:aws:iam::123456789012:role/Task is not authorized, "
                             "password=hunter2",
    "ExpiredTokenException": "The security token included in the request is expired",
    "ThrottlingException": "Rate exceeded",
}


def catalog():
    from botocore.exceptions import ClientError
    from mcp.server.fastmcp import FastMCP

    app = FastMCP("phase3-fixture")

    @app.tool()
    def search_trips(limit: int = 5) -> dict:
        """Filter trips."""
        return {"packages": [], "sql": "SELECT 1", "parameters": []}

    @app.tool()
    def compare_packages(package_ids: list[str]) -> list:
        """Compare trips."""
        return []

    @app.tool()
    def price_range(destination: str) -> dict:
        """Fails on the server with text that must never reach the model."""
        raise RuntimeError(LEAK)

    @app.tool()
    def region_inventory(region: str) -> dict:
        """Ends the server process mid call, so the client reports the failure itself."""
        os._exit(1)

    @app.tool()
    def currency_convert(amount: float, from_ccy: str, to_ccy: str) -> dict:
        """Answers a currency it does not know with a normal result."""
        return {"error": "unsupported_currency", "currency": to_ccy}

    @app.tool()
    def loyalty_balance(travelerId: str, program: str) -> dict:
        """Loyalty balance; a program named for an AWS error code raises that error."""
        if program in BOTO_ERRORS:
            raise ClientError(
                {"Error": {"Code": program, "Message": BOTO_ERRORS[program]}}, "ExecuteStatement")
        return {"traveler_id": travelerId, "program": program, "points_balance": 1}

    return app


def write_pid_atomically(target: Path) -> None:
    """Make the pid file appear whole, so a reader never sees a half-written one."""
    partial = target.with_name(target.name + ".partial")
    partial.write_text(str(os.getpid()))
    os.replace(partial, target)


if __name__ == "__main__":
    if sys.argv[1:2] == ["silent"]:
        write_pid_atomically(Path(sys.argv[2]))
        time.sleep(600)
    else:
        catalog().run(transport="stdio")
