"""A server that never answers ``initialize`` must not hang the per-turn client."""

import os
import sys
import time

import pytest
from mcp import StdioServerParameters

from backend.agents.phase_03_retrieval import mcp_session
from backend.mcp import concierge_mcp_client

SILENT_SERVER = (
    "import os, sys, time\n"
    "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
    "time.sleep(120)\n"
)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def test_connect_times_out_and_leaves_no_child(monkeypatch, tmp_path):
    pid_file = tmp_path / "child.pid"
    monkeypatch.setattr(
        concierge_mcp_client,
        "concierge_server_parameters",
        lambda: StdioServerParameters(
            command=sys.executable, args=["-c", SILENT_SERVER, str(pid_file)]
        ),
    )
    monkeypatch.setattr(mcp_session, "MCP_START_TIMEOUT_SECONDS", 1)
    client = concierge_mcp_client.MeridianConciergeMCPClient()

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await client.connect()

    assert time.monotonic() - started < 10
    assert client.session is None
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 5
    while alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not alive(pid)
