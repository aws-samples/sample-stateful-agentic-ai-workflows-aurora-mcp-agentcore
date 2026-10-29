"""The memory MCP demo fails when the authorization negative control is allowed."""

import asyncio

import pytest

from examples import memory_mcp_demo as demo


class AllowingClient:
    tools = [{"name": "recall_traveler_profile", "description": "profile"}]

    async def connect(self):
        return None

    async def disconnect(self):
        return None

    async def call(self, name, arguments):
        return {"ok": True}


def test_an_allowed_decoy_read_exits_non_zero(monkeypatch):
    monkeypatch.setattr(demo, "MeridianMemoryMCPClient", AllowingClient)
    with pytest.raises(SystemExit) as exit_info:
        asyncio.run(demo.run("trv_meridian_demo", "conv_meridian_demo"))
    assert exit_info.value.code == 1
