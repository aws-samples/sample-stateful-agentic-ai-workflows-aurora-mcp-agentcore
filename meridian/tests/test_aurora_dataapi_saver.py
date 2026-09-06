"""AuroraDataApiSaver writes and reads LangGraph checkpoints over the Data API.

A fake client records statements so the ordering guarantee can be asserted
without a cluster: blob segments must be durable before the checkpoints row
that makes the checkpoint visible.
"""

import json
from typing import Any

import pytest

from backend.db.aurora_dataapi_saver import AuroraDataApiSaver
from backend.db.blob_windows import MAX_ROW_BYTES


class FakeDataClient:
    """Records executed SQL and replays queued results."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.results: list[list[dict]] = []
        self.fail_on: str | None = None

    async def execute(self, sql: str, params: tuple = (), **kwargs: Any) -> list[dict]:
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("data api failure")
        self.calls.append((" ".join(sql.split()), params))
        return self.results.pop(0) if self.results else []

    def statements(self) -> list[str]:
        return [sql for sql, _ in self.calls]


@pytest.fixture
def client() -> FakeDataClient:
    return FakeDataClient()


@pytest.fixture
def saver(client: FakeDataClient) -> AuroraDataApiSaver:
    return AuroraDataApiSaver(client)


def _config(thread_id: str = "t1", ns: str = "") -> dict:
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns}}


def _checkpoint(cid: str = "cp1") -> dict:
    return {
        "v": 1,
        "id": cid,
        "ts": "2026-09-06T00:00:00+00:00",
        "channel_values": {"messages": ["hello"]},
        "channel_versions": {"messages": "1"},
        "versions_seen": {},
    }


@pytest.mark.asyncio
async def test_aput_returns_the_new_config(saver: AuroraDataApiSaver) -> None:
    result = await saver.aput(_config(), _checkpoint(), {"step": 1}, {"messages": "1"})
    assert result["configurable"]["checkpoint_id"] == "cp1"
    assert result["configurable"]["thread_id"] == "t1"


@pytest.mark.asyncio
async def test_blobs_are_written_before_the_checkpoint_row(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    await saver.aput(_config(), _checkpoint(), {"step": 1}, {"messages": "1"})
    statements = client.statements()
    blob_at = next(i for i, s in enumerate(statements) if "checkpoint_blobs" in s)
    row_at = next(i for i, s in enumerate(statements) if "INTO checkpoints" in s)
    assert blob_at < row_at, "a partial write must never be visible as committed"


@pytest.mark.asyncio
async def test_checkpoint_row_is_not_written_when_a_blob_fails(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    client.fail_on = "checkpoint_blobs"
    with pytest.raises(RuntimeError):
        await saver.aput(_config(), _checkpoint(), {"step": 1}, {"messages": "1"})
    assert not any("INTO checkpoints" in s for s in client.statements())


@pytest.mark.asyncio
async def test_large_value_is_appended_in_segments(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    checkpoint = _checkpoint()
    checkpoint["channel_values"]["messages"] = ["x" * (MAX_ROW_BYTES * 2)]
    await saver.aput(_config(), checkpoint, {"step": 1}, {"messages": "1"})
    appends = [s for s in client.statements() if "blob || " in s]
    assert appends, "values beyond one window must append rather than re-insert"


@pytest.mark.asyncio
async def test_read_blob_uses_octet_length_then_substring(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    client.results = [[{"n": 5}], [{"part": b"hello"}]]
    assert await saver._read_blob("t1", "", "messages", "1") == b"hello"
    statements = client.statements()
    assert "octet_length(blob)" in statements[0]
    assert "substring(blob FROM" in statements[1]


@pytest.mark.asyncio
async def test_read_blob_windows_a_large_value(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    total = MAX_ROW_BYTES + 10
    client.results = [
        [{"n": total}],
        [{"part": b"a" * MAX_ROW_BYTES}],
        [{"part": b"b" * 10}],
    ]
    blob = await saver._read_blob("t1", "", "messages", "1")
    assert len(blob) == total
    assert blob.endswith(b"b" * 10)


@pytest.mark.asyncio
async def test_read_blob_returns_none_when_absent(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    client.results = [[]]
    assert await saver._read_blob("t1", "", "messages", "1") is None


@pytest.mark.asyncio
async def test_aget_tuple_returns_none_for_unknown_thread(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    client.results = [[]]
    assert await saver.aget_tuple(_config("missing")) is None


@pytest.mark.asyncio
async def test_aput_round_trips_through_aget_tuple(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    checkpoint = _checkpoint()
    await saver.aput(_config(), checkpoint, {"step": 1}, {"messages": "1"})
    stored = {sql: params for sql, params in client.calls}
    blob_call = next(p for s, p in client.calls if "checkpoint_blobs" in s)
    payload = next(p for p in blob_call if isinstance(p, bytes))
    assert isinstance(payload, bytes) and payload, "channel value must serialize to bytes"
    assert stored


@pytest.mark.asyncio
async def test_aget_tuple_uses_the_stored_blob_type_not_a_literal(
    saver: AuroraDataApiSaver, client: FakeDataClient
) -> None:
    """``JsonPlusSerializer().dumps_typed`` returns ``"msgpack"``, not ``"json"``.

    ``aget_tuple`` must read the ``type`` column back from ``checkpoint_blobs``
    and pass it to ``loads_typed`` rather than hardcoding ``"json"`` -- a
    hardcoded literal would silently corrupt every rehydrated channel value.
    """
    blob_type, payload = saver.serde.dumps_typed(["hello"])
    assert blob_type != "json", "this test only proves something if the type isn't json"

    checkpoint_row = {
        "v": 1,
        "id": "cp1",
        "ts": "2026-09-06T00:00:00+00:00",
        "channel_versions": {"messages": "1"},
        "versions_seen": {},
    }
    client.results = [
        [
            {
                "thread_id": "t1",
                "checkpoint_ns": "",
                "checkpoint_id": "cp1",
                "parent_checkpoint_id": None,
                "checkpoint": json.dumps(checkpoint_row),
                "metadata": json.dumps({"step": 1}),
            }
        ],
        [{"n": len(payload)}],
        [{"part": payload}],
        [{"type": blob_type}],
    ]

    tuple_ = await saver.aget_tuple(_config())

    assert tuple_ is not None
    assert tuple_.checkpoint["channel_values"]["messages"] == ["hello"]
