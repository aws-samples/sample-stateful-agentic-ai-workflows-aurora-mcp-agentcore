"""The lost-response proof ends by giving the hold back through the Gateway."""

import json

import pytest

from scripts.lost_response_proof import check_release

HOLD = {"hold_request_id": "hrq_1", "booking_id": "bk_1"}


class Client:
    def __init__(self, status):
        self._status = status

    async def execute(self, sql, params):
        return [{"status": self._status}]


def call_tool(name, arguments):
    body = {"release": {"status": "released", "replayed": False}}
    return {"result": {"content": [{"type": "text", "text": json.dumps(body)}]}}


async def test_the_step_passes_when_aurora_shows_the_booking_released():
    await check_release(Client("released"), "jrn_1", HOLD, call_tool)


async def test_the_step_fails_when_aurora_still_shows_the_booking_held():
    with pytest.raises(AssertionError, match="Aurora still shows"):
        await check_release(Client("held"), "jrn_1", HOLD, call_tool)


async def test_the_step_fails_when_the_gateway_does_not_accept_the_release():
    def refused(name, arguments):
        return {"result": {"isError": True, "content": [
            {"type": "text", "text": "Tool Execution Denied: no policy"}]}}

    with pytest.raises(AssertionError, match="not accepted"):
        await check_release(Client("released"), "jrn_1", HOLD, refused)
