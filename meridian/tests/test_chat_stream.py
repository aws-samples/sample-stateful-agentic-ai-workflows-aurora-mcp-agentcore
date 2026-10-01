"""Streaming transport must preserve auth, completion, and no-replay boundaries."""
import asyncio
import json
from io import BytesIO

import pytest
from fastapi import HTTPException

from backend.agentcore.runtime import AgentCoreRuntimeAdapter, iter_sse
from backend.chat_stream import chat_event_sink, emit_chat_event
from backend.http_auth import HttpPrincipal
from backend.routers import chat as route

PRINCIPAL = HttpPrincipal(subject_id="test", traveler_id="trv_meridian_demo", authentication="test")


def frame(event):
    return f"data: {json.dumps(event, ensure_ascii=False)}\r\n\r\n".encode()


def test_runtime_frames_decode_split_utf8_and_double_encoding():
    raw = frame(json.dumps({"type": "token", "text": "Malé 東京"}, ensure_ascii=False))
    assert list(iter_sse([bytes([byte]) for byte in raw])) == [{"type": "token", "text": "Malé 東京"}]


def test_runtime_forwards_before_reading_completion_and_closes_body(monkeypatch):
    observed = []
    chunks = iter([frame({"type": "token", "text": "Tokyo"}), frame({"type": "result", "message": "Tokyo"}), b""])
    class Body(BytesIO):
        def read(self, size=-1):
            chunk = next(chunks)
            if b'result' in chunk:
                assert observed == [{"type": "delta", "text": "Tokyo"}]
            return chunk
    body = Body()
    class Client:
        def invoke_agent_runtime(self, **kwargs):
            return {"response": body}
    adapter = AgentCoreRuntimeAdapter(runtime_arn="arn:test/runtime/demo")
    adapter._client = Client()
    token = chat_event_sink.set(observed.append)
    try:
        result = adapter.invoke_turn("conversation", "traveler", "Tokyo", "", budget_ceiling_cents=1, travelers_count=1)
    finally:
        chat_event_sink.reset(token)
    assert result.message == "Tokyo"
    assert body.closed


@pytest.mark.asyncio
async def test_stream_delivers_delta_before_completion(monkeypatch):
    release = asyncio.Event()
    async def chat(request, principal):
        await asyncio.to_thread(emit_chat_event, {"type": "delta", "text": "Tokyo"})
        await release.wait()
        return route.ChatResponse(message="Tokyo is ready.", activities=[])
    monkeypatch.setattr(route, "chat", chat)
    response = await route.stream_chat(route.ChatRequest(message="Tokyo", phase=4), PRINCIPAL)
    iterator = response.body_iterator
    assert 'Connecting' in await anext(iterator)
    assert 'Tokyo' in await asyncio.wait_for(anext(iterator), 1)
    release.set()
    assert 'complete' in await asyncio.wait_for(anext(iterator), 1)
    await iterator.aclose()
    assert chat_event_sink.get() is None


@pytest.mark.asyncio
async def test_stream_disconnect_finishes_memory_work_without_replaying(monkeypatch):
    release, saved = asyncio.Event(), asyncio.Event()
    calls = 0
    async def chat(request, principal):
        nonlocal calls
        calls += 1
        emit_chat_event({"type": "delta", "text": "Partial"})
        await release.wait()
        saved.set()
        return route.ChatResponse(message="Final", activities=[])
    monkeypatch.setattr(route, "chat", chat)
    response = await route.stream_chat(route.ChatRequest(message="Tokyo", phase=4), PRINCIPAL)
    iterator = response.body_iterator
    await anext(iterator)
    await anext(iterator)
    await iterator.aclose()
    release.set()
    await asyncio.wait_for(saved.wait(), 1)
    assert calls == 1


@pytest.mark.asyncio
async def test_stream_suppresses_overridden_workflow_prose(monkeypatch):
    async def chat(request, principal):
        emit_chat_event({"type": "delta", "text": "Must not appear"})
        return route.ChatResponse(message="Switch to Workflow", activities=[])
    monkeypatch.setattr(route, "chat", chat)
    response = await route.stream_chat(route.ChatRequest(message="My flight was cancelled. Rework the trip, then check duration availability.", phase=4), PRINCIPAL)
    events = [event async for event in response.body_iterator]
    assert len(events) == 2
    assert 'Must not appear' not in ''.join(events)
    assert 'Switch to Workflow' in events[-1]


@pytest.mark.asyncio
async def test_stream_denies_wrong_traveler_before_headers():
    with pytest.raises(HTTPException) as error:
        await route.stream_chat(route.ChatRequest(message="Tokyo", phase=4, customer_id="someone-else"), PRINCIPAL)
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_stream_errors_are_terminal_not_success(monkeypatch):
    async def chat(request, principal):
        emit_chat_event({"type": "delta", "text": "Partial"})
        raise RuntimeError("Private exception")
    monkeypatch.setattr(route, "chat", chat)
    response = await route.stream_chat(route.ChatRequest(message="Tokyo", phase=4), PRINCIPAL)
    events = [event async for event in response.body_iterator]
    assert '"type": "error"' in events[-1]
    assert 'Private exception' not in ''.join(events)
    assert '"type": "complete"' not in ''.join(events)


def test_runtime_previews_only_catalog_ids_before_completion():
    from backend.agentcore.runtime import _forward_runtime_events

    observed = []
    packages = {"type": "packages", "packages": [
        {"package_id": "CTY-002", "private_payload": "not for UI"},
        {"package_id": "CTY-002"}, {"package_id": 42}, None,
    ]}
    token = chat_event_sink.set(observed.append)
    try:
        events = _forward_runtime_events({"response": [frame(packages), frame({"type": "result"})]})
        assert next(events) == packages
        assert observed == [{"type": "candidates", "package_ids": ["CTY-002"]}]
        assert next(events)["type"] == "result"
    finally:
        chat_event_sink.reset(token)


@pytest.mark.parametrize("existing_break", ["", "\n", "\n\n"])
def test_tool_steps_separate_streamed_paragraphs_and_final_answer(existing_break):
    from backend.agentcore.runtime import _forward_runtime_events

    observed = []
    first = "Let me check Tokyo." + existing_break
    second = "Here are your options."
    events = [
        {"type": "token", "text": first},
        {"type": "activity", "title": "Catalog search"},
        {"type": "activity", "title": "Availability"},
        {"type": "token", "text": second},
        {"type": "result", "message": first + second},
    ]
    token = chat_event_sink.set(observed.append)
    try:
        final = list(_forward_runtime_events({"response": [frame(e) for e in events]}))[-1]
    finally:
        chat_event_sink.reset(token)
    expected = "Let me check Tokyo.\n\nHere are your options."
    assert "".join(e["text"] for e in observed if e["type"] == "delta") == expected
    assert final["message"] == expected


def test_stream_formatting_never_overwrites_an_authoritative_correction():
    from backend.agentcore.runtime import _forward_runtime_events

    events = [{"type": "token", "text": "Checking trips."},
              {"type": "activity", "title": "Availability"},
              {"type": "token", "text": "Found Tokyo."},
              {"type": "result", "message": "No available trips remain."}]
    final = list(_forward_runtime_events({"response": [frame(e) for e in events]}))[-1]
    assert final["message"] == "No available trips remain."


def test_stream_and_persisted_reply_share_the_direct_voice():
    from backend.agentcore.runtime import _forward_runtime_events

    observed = []
    events = [{"type": "token", "text": "Gr"},
              {"type": "token", "text": "eat! I can check Tokyo."},
              {"type": "activity", "title": "Catalog search"},
              {"type": "token", "text": "Perfect! Two trips are available."},
              {"type": "result", "message": "Great! I can check Tokyo.Perfect! Two trips are available."}]
    token = chat_event_sink.set(observed.append)
    try:
        final = list(_forward_runtime_events({"response": [frame(e) for e in events]}))[-1]
    finally:
        chat_event_sink.reset(token)
    expected = "I can check Tokyo.\n\nTwo trips are available."
    assert "".join(e["text"] for e in observed if e["type"] == "delta") == expected
    assert final["message"] == expected
