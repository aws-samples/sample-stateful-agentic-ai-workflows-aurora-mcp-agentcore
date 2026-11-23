"""Hold, booking and Phase 5 stream with keepalives, so no call outlasts the browser's wait.

Every test runs on a loop whose clock jumps to the next timer, so a 70 s call takes no time.
"""
import asyncio
import json
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.agents.phase_04_production.write_outcome import (
    PlacedWriteUnfinished, WriteOutcomeUnknown,
)
from backend.http_auth import HttpPrincipal
from backend.routers import chat as route

APP_RUNNER_REQUEST_LIMIT_SECONDS = 120
PRINCIPAL = HttpPrincipal(subject_id="test", traveler_id="trv_meridian_demo", authentication="test")
HOLD = route.OrderRequest(product_id="CTY-002", phase=4, conversation_id="conv_hold_1")
BOOK = route.BookingRequest(booking_id="bkg_1", conversation_id="conv_hold_1")


class VirtualClockLoop(asyncio.SelectorEventLoop):
    """An event loop that advances its own clock instead of sleeping when it is idle."""

    def __init__(self):
        super().__init__()
        self._skipped = 0.0
        real_select = self._selector.select

        def select(timeout=None):
            if timeout:
                self._skipped += timeout
                timeout = 0
            return real_select(timeout)

        self._selector.select = select

    def time(self):
        return super().time() + self._skipped


def run_virtual(coroutine):
    loop = VirtualClockLoop()
    try:
        return loop.run_until_complete(coroutine)
    finally:
        loop.close()


async def collect(response):
    return [frame async for frame in response.body_iterator]


def terminal(frames):
    events = [json.loads(f[len("data: "):]) for f in frames if f.startswith("data: ")]
    return events[-1]


def order_response(message="Hold placed."):
    return route.OrderResponse(message=message, order=None, activities=[])


def test_a_hold_that_takes_70_seconds_streams_keepalives_and_resolves(monkeypatch):
    audits = []

    async def slow_hold(request, *, authorization_subject=None):
        await asyncio.sleep(70)
        audits.append(request.conversation_id)
        return order_response()

    monkeypatch.setattr(route, "production_hold", slow_hold)

    async def scenario():
        response = await route.stream_order(HOLD, PRINCIPAL)
        return await collect(response)

    frames = run_virtual(scenario())
    assert sum(f.startswith(": keepalive") for f in frames) >= 6
    assert terminal(frames) == {
        "type": "complete", "response": order_response().model_dump(mode="json"),
    }
    assert audits == ["conv_hold_1"]


def test_a_booking_that_takes_70_seconds_streams_keepalives_and_resolves(monkeypatch):
    async def slow_booking(request, *, authorization_subject=None):
        await asyncio.sleep(70)
        return route.BookingResponse(message="Booked.", order=None, activities=[])

    monkeypatch.setattr(route, "production_booking", slow_booking)

    async def scenario():
        return await collect(await route.stream_book(BOOK, PRINCIPAL))

    frames = run_virtual(scenario())
    assert sum(f.startswith(": keepalive") for f in frames) >= 6
    assert terminal(frames)["type"] == "complete"
    assert terminal(frames)["response"]["message"] == "Booked."


def test_a_phase_5_turn_that_takes_70_seconds_streams_and_resolves(monkeypatch):
    async def slow_chat(request, principal):
        await asyncio.sleep(70)
        return route.ChatResponse(message="Recovery paused.", activities=[])

    monkeypatch.setattr(route, "chat", slow_chat)

    async def scenario():
        request = route.ChatRequest(message="Plan a recovery", phase=5)
        return await collect(await route.stream_chat(request, PRINCIPAL))

    frames = run_virtual(scenario())
    assert sum(f.startswith(": keepalive") for f in frames) >= 6
    assert terminal(frames)["response"]["message"] == "Recovery paused."


def _tool_failure_activity():
    return route.create_activity(
        activity_type="error", title="Tool call failed",
        details="The gateway returned no result.", agent_name="ProductionAgent",
        agent_file="runtime",
    )


def _stream_terminal(monkeypatch, **request_fields):
    """Run the real ``chat`` through ``stream_chat`` and return the terminal event."""
    async def scenario():
        request = route.ChatRequest(message="Tokyo", **request_fields)
        return terminal(await collect(await route.stream_chat(request, PRINCIPAL)))

    return run_virtual(scenario())


def test_a_recovered_tool_error_streams_as_a_complete_turn(monkeypatch):
    async def recovered(request, principal):
        return route.ChatResponse(
            message="Here are three Tokyo trips that fit.", activities=[_tool_failure_activity()],
        )

    monkeypatch.setattr(route, "chat", recovered)
    event = _stream_terminal(monkeypatch, phase=4, experience="concierge")
    assert event["type"] == "complete"
    assert event["response"]["message"] == "Here are three Tokyo trips that fit."
    assert "failed" not in event["response"]


def test_a_runtime_that_answers_with_an_error_span_is_complete(monkeypatch):
    async def production_search(*args, **kwargs):
        return [], [_tool_failure_activity()], "Two options remain.", "conv_1", []

    monkeypatch.setattr(route, "production_search", production_search)
    event = _stream_terminal(monkeypatch, phase=4, experience="concierge")
    assert event["type"] == "complete"
    assert event["response"]["message"] == "Two options remain."


@pytest.mark.parametrize("raised", [
    RuntimeError("Runtime ended the turn"),
    WriteOutcomeUnknown("hold"),
    PlacedWriteUnfinished("hold", "audit"),
])
def test_a_runtime_turn_that_raises_streams_as_an_error(monkeypatch, raised):
    async def production_search(*args, **kwargs):
        raise raised

    monkeypatch.setattr(route, "production_search", production_search)
    event = _stream_terminal(monkeypatch, phase=4, experience="concierge")
    assert event["type"] == "error"
    assert event["message"] == "I encountered an error in Production mode. Please try again."


def test_an_unconfigured_agentcore_streams_as_an_error(monkeypatch):
    from backend.agentcore.errors import AgentCoreNotConfiguredError

    async def production_search(*args, **kwargs):
        raise AgentCoreNotConfiguredError(("runtime",), "project", ())

    monkeypatch.setattr(route, "production_search", production_search)
    event = _stream_terminal(monkeypatch, phase=4, experience="concierge")
    assert event["type"] == "error"
    assert "deployed AgentCore Runtime, Gateway" in event["message"]


def test_a_failed_phase_5_turn_streams_as_an_error(monkeypatch):
    async def broken_workflow(*args, **kwargs):
        raise RuntimeError("graph failed")

    monkeypatch.setattr(route, "run_workflow_turn", broken_workflow)
    event = _stream_terminal(monkeypatch, phase=5, experience="concierge")
    assert event["type"] == "error"
    assert event["status"] == 503


def test_the_failed_flag_never_reaches_the_json_response():
    response = route.ChatResponse(message="x", activities=[], failed=True)
    assert "failed" not in response.model_dump(mode="json")
    assert "failed" not in route.ChatResponse.model_json_schema(mode="serialization")["properties"]


def test_the_ladder_keeps_its_response_even_when_the_turn_failed(monkeypatch):
    async def failed_chat(request, principal):
        return route.ChatResponse(message="Could not run.", activities=[], failed=True)

    monkeypatch.setattr(route, "chat", failed_chat)
    assert _stream_terminal(monkeypatch, phase=4)["type"] == "complete"
    assert _stream_terminal(monkeypatch, phase=4, experience="concierge")["type"] == "error"


def test_streaming_is_refused_for_phases_that_finish_quickly():
    async def scenario():
        with pytest.raises(HTTPException) as refused:
            await route.stream_chat(route.ChatRequest(message="Tokyo", phase=3), PRINCIPAL)
        return refused.value.status_code

    assert run_virtual(scenario()) == 422


@pytest.mark.parametrize("write, raised", [
    ("hold", PlacedWriteUnfinished("hold", "audit")),
    ("hold", WriteOutcomeUnknown("hold")),
    ("booking", PlacedWriteUnfinished("booking", "memory")),
    ("booking", WriteOutcomeUnknown("booking")),
    ("hold", RuntimeError("refused")),
])
def test_a_failed_write_ends_with_the_status_code_and_words_of_the_plain_route(
    monkeypatch, write, raised,
):
    wanted = route._governed_write_reply(write, raised)

    async def fail(request, *, authorization_subject=None):
        raise wanted

    monkeypatch.setattr(route, "production_hold" if write == "hold" else "production_booking", fail)

    async def scenario():
        stream = (route.stream_order(HOLD, PRINCIPAL) if write == "hold"
                  else route.stream_book(BOOK, PRINCIPAL))
        return terminal(await collect(await stream))

    assert run_virtual(scenario()) == {
        "type": "error", "message": wanted.detail, "status": 503, "code": "governed_write",
    }


def test_a_refusal_keeps_its_status_and_has_no_governed_write_code(monkeypatch):
    async def refuse(request, *, authorization_subject=None):
        raise HTTPException(status_code=403, detail="Not your hold.")

    monkeypatch.setattr(route, "production_hold", refuse)

    async def scenario():
        return terminal(await collect(await route.stream_order(HOLD, PRINCIPAL)))

    assert run_virtual(scenario()) == {"type": "error", "message": "Not your hold.", "status": 403}


def test_another_travelers_hold_is_refused_before_any_stream_starts(access_writer):
    async def scenario():
        other = HOLD.model_copy(update={"traveler_id": "someone-else"})
        with pytest.raises(HTTPException) as refused:
            await route.stream_order(other, PRINCIPAL)
        return refused.value.status_code

    assert run_virtual(scenario()) == 403


def test_a_caller_that_leaves_does_not_cancel_the_hold_or_its_audit_step(monkeypatch):
    finished = []

    async def slow_hold(request, *, authorization_subject=None):
        await asyncio.sleep(70)
        finished.append("audit written")
        return order_response()

    monkeypatch.setattr(route, "production_hold", slow_hold)

    async def scenario():
        response = await route.stream_order(HOLD, PRINCIPAL)
        iterator = response.body_iterator
        await anext(iterator)
        await iterator.aclose()
        await asyncio.sleep(80)
        return list(finished)

    assert run_virtual(scenario()) == ["audit written"]


def _number_in(path: str, pattern: str) -> float:
    text = (Path(__file__).resolve().parents[1] / path).read_text(encoding="utf-8")
    found = re.search(pattern, text)
    assert found, f"{path} no longer matches {pattern}"
    return float(found.group(1).replace("_", ""))


def test_a_hold_that_outlasts_the_ceiling_ends_the_stream_with_a_read_back_and_writes_once(
    monkeypatch,
):
    writes = []

    async def very_slow_hold(request, *, authorization_subject=None):
        writes.append("placed")
        await asyncio.sleep(300)
        writes.append("audit written")
        return order_response()

    monkeypatch.setattr(route, "production_hold", very_slow_hold)

    async def scenario():
        loop = asyncio.get_running_loop()
        started = loop.time()
        frames = await collect(await route.stream_order(HOLD, PRINCIPAL))
        ended_after = loop.time() - started
        seen_at_close = list(writes)
        await asyncio.sleep(400)
        return frames, ended_after, seen_at_close, list(writes)

    frames, ended_after, seen_at_close, writes_after = run_virtual(scenario())
    assert ended_after == pytest.approx(route.STREAM_CEILING_SECONDS, abs=1)
    assert terminal(frames)["code"] == "read_back" and "status" not in terminal(frames)
    assert not any('"type": "complete"' in f for f in frames)
    assert seen_at_close == ["placed"]
    assert writes_after == ["placed", "audit written"], "the work finished, once, after the close"


def test_a_reply_that_finished_before_the_ceiling_is_delivered_not_read_back(monkeypatch):
    async def quick_hold(request, *, authorization_subject=None):
        await asyncio.sleep(50)
        return order_response("Held at 50 s.")

    monkeypatch.setattr(route, "production_hold", quick_hold)

    async def scenario():
        response = await route.stream_order(HOLD, PRINCIPAL)
        iterator = response.body_iterator
        frames = [await anext(iterator)]
        await asyncio.sleep(route.STREAM_CEILING_SECONDS + 10)
        frames += [frame async for frame in iterator]
        return frames

    last = terminal(run_virtual(scenario()))
    assert last["type"] == "complete" and last["response"]["message"] == "Held at 50 s."


def test_nothing_queues_once_the_page_has_gone():
    async def scenario():
        listener = route._Listener(None)
        frames = listener.frames()
        await anext(frames)
        await frames.aclose()
        listener.deliver({"type": "delta", "text": "late"})
        return listener.queue.qsize()

    assert run_virtual(scenario()) == 0


def test_the_waits_nest_inside_the_limits_that_watch_them():
    """Keepalive < idle wait, and 2 x keepalive < CloudFront's gap; ceiling < App Runner's 120 s."""
    request_ts = "frontend/src/api/request.ts"
    idle = _number_in(request_ts, r"STREAM_IDLE_TIMEOUT_MS = ([\d_]+)") / 1000
    ceiling = _number_in(request_ts, r"STREAM_HARD_CAP_MS = ([\d_]+)") / 1000
    cloudfront = _number_in("infra/lib/meridian-web-stack.ts",
                            r"readTimeout: Duration\.seconds\((\d+)\)")
    assert route.STREAM_KEEPALIVE_SECONDS < idle
    assert route.STREAM_KEEPALIVE_SECONDS * 2 < cloudfront
    assert ceiling < APP_RUNNER_REQUEST_LIMIT_SECONDS
    assert route.STREAM_CEILING_SECONDS == ceiling, "the server and the browser share one ceiling"
