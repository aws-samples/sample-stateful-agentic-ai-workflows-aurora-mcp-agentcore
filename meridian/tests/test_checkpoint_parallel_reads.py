"""Independent checkpoint reads yield, stay bounded, and preserve every value."""

import asyncio
from types import SimpleNamespace

import pytest

from backend.db.aurora_dataapi_saver import AuroraDataApiSaver


@pytest.mark.parametrize("fail", [False, True])
async def test_channel_reads_are_bounded_and_drained_before_return(monkeypatch, fail):
    saver = AuroraDataApiSaver(None)
    active = peak = finished = 0

    async def read_blob(_thread, _namespace, channel, _version):
        nonlocal active, peak, finished
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0)
            if fail and channel == "channel-3":
                raise RuntimeError("read failed")
            return saver.serde.dumps_typed(channel)
        finally:
            active -= 1
            finished += 1

    monkeypatch.setattr(saver, "_read_blob", read_blob)
    versions = {f"channel-{i}": "1" for i in range(12)}
    if fail:
        with pytest.raises(RuntimeError, match="read failed"):
            await saver._load_channel_values("thread", "", versions)
    else:
        values = await saver._load_channel_values("thread", "", versions)
        assert values == {channel: channel for channel in versions}
    assert 1 < peak <= 4
    assert active == 0
    assert finished == len(versions)


async def test_parallel_pending_reads_preserve_metadata_order(monkeypatch):
    saver = AuroraDataApiSaver(None)
    encoded = [saver.serde.dumps_typed(i) for i in range(6)]

    async def metadata(*args):
        return [
            {"task_id": str(i), "idx": i - 1, "channel": f"ch-{i}",
             "type": kind, "n": len(blob)}
            for i, (kind, blob) in enumerate(encoded)
        ]

    async def read_write(key, _channel, _total):
        # Finish later metadata rows before earlier rows.
        for _ in range(6 - int(key[3])):
            await asyncio.sleep(0)
        return encoded[int(key[3])][1]

    saver.client = SimpleNamespace(execute=metadata)
    monkeypatch.setattr(saver, "_read_write_blob", read_write)
    pending = await saver._pending_writes("thread", "", "checkpoint")
    assert pending == [(str(i), f"ch-{i}", i) for i in range(6)]
