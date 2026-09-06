"""Vendored from langchain-ai/langgraph, libs/checkpoint/tests/test_memory.py,
tag checkpoint==4.1.1. See tests/conformance/README.md for provenance and for
the list of adaptations made to point this suite at AuroraDataApiSaver
instead of InMemorySaver.
"""

import logging
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from langgraph.checkpoint.base import (
    Checkpoint,
    CheckpointMetadata,
    create_checkpoint,
    empty_checkpoint,
)
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import (
    JsonPlusSerializer,
    _warned_blocked_types,
    _warned_unregistered_types,
)

from backend.db.aurora_dataapi_saver import AuroraDataApiSaver
from tests.conformance.fake_cluster import FakeCluster


class MemoryPydantic(BaseModel):
    foo: str


@pytest.fixture(autouse=True)
def _reset_warned_types() -> None:
    # Warning dedup state is process-global; reset per-test so each case sees
    # a fresh slate and assertions about warning emission are stable.
    _warned_unregistered_types.clear()
    _warned_blocked_types.clear()


class TestMemorySaver:
    @pytest.fixture(autouse=True)
    def setup(self) -> None:
        self.memory_saver = AuroraDataApiSaver(FakeCluster())

        # objects for test setup
        self.config_1: RunnableConfig = {
            "configurable": {
                "thread_id": "thread-1",
                "checkpoint_ns": "",
                "checkpoint_id": "1",
            }
        }
        self.config_2: RunnableConfig = {
            "configurable": {
                "thread_id": "thread-2",
                "checkpoint_ns": "",
                "checkpoint_id": "2",
            }
        }
        self.config_3: RunnableConfig = {
            "configurable": {
                "thread_id": "thread-2",
                "checkpoint_id": "2-inner",
                "checkpoint_ns": "inner",
            }
        }

        self.chkpnt_1: Checkpoint = empty_checkpoint()
        self.chkpnt_2: Checkpoint = create_checkpoint(self.chkpnt_1, {}, 1)
        self.chkpnt_3: Checkpoint = empty_checkpoint()

        self.metadata_1: CheckpointMetadata = {
            "source": "input",
            "step": 2,
            "writes": {},
            "score": 1,
        }
        self.metadata_2: CheckpointMetadata = {
            "source": "loop",
            "step": 1,
            "writes": {"foo": "bar"},
            "score": None,
        }
        self.metadata_3: CheckpointMetadata = {}

    @pytest.mark.skip(
        reason=(
            "InMemorySaver.put stores metadata via get_checkpoint_metadata, "
            "which keeps a 'writes' key; the entire Postgres family "
            "(PostgresSaver, AsyncPostgresSaver, ShallowPostgresSaver) instead "
            "uses get_serializable_checkpoint_metadata, which pops 'writes' "
            "before JSONB storage -- confirmed in "
            "langgraph.checkpoint.postgres.{__init__,aio,shallow}. "
            "AuroraDataApiSaver.aput deliberately calls the same "
            "get_serializable_checkpoint_metadata for schema/byte "
            "compatibility with that family, so it correctly does NOT retain "
            "'writes' either. This test assumes InMemorySaver's own choice, "
            "not a BaseCheckpointSaver contract. See task-6-report.md."
        )
    )
    async def test_combined_metadata(self) -> None:
        # Adapted: upstream calls .put()/.get_tuple() synchronously.
        # AuroraDataApiSaver is async-only by design (every other test in this
        # project only exercises aput/aget_tuple/aput_writes/alist), so this
        # calls the async equivalents; the assertions are unchanged.
        config: RunnableConfig = {
            "configurable": {
                "thread_id": "thread-2",
                "checkpoint_ns": "",
                "__super_private_key": "super_private_value",
            },
            "metadata": {"run_id": "my_run_id"},
        }
        await self.memory_saver.aput(
            config, self.chkpnt_2, self.metadata_2, self.chkpnt_2["channel_versions"]
        )
        checkpoint = await self.memory_saver.aget_tuple(config)
        assert checkpoint is not None
        assert checkpoint.metadata == {
            **self.metadata_2,
            "run_id": "my_run_id",
        }

    @pytest.mark.skip(
        reason=(
            "AuroraDataApiSaver.alist deliberately does not implement metadata "
            "filtering (see its docstring and "
            "tests/test_aurora_dataapi_saver_writes.py::test_alist_raises_for_"
            "nonempty_filter) -- a documented, already-tested design limit, not "
            "a gap this task should close. See task-6-report.md."
        )
    )
    async def test_search(self) -> None:
        # set up test
        # save checkpoints
        self.memory_saver.put(
            self.config_1,
            self.chkpnt_1,
            self.metadata_1,
            self.chkpnt_1["channel_versions"],
        )
        self.memory_saver.put(
            self.config_2,
            self.chkpnt_2,
            self.metadata_2,
            self.chkpnt_2["channel_versions"],
        )
        self.memory_saver.put(
            self.config_3,
            self.chkpnt_3,
            self.metadata_3,
            self.chkpnt_3["channel_versions"],
        )

        # call method / assertions
        query_1 = {"source": "input"}  # search by 1 key
        query_2 = {
            "step": 1,
            "writes": {"foo": "bar"},
        }  # search by multiple keys
        query_3: dict[str, Any] = {}  # search by no keys, return all checkpoints
        query_4 = {"source": "update", "step": 1}  # no match

        search_results_1 = list(self.memory_saver.list(None, filter=query_1))
        assert len(search_results_1) == 1
        assert search_results_1[0].metadata == self.metadata_1

        search_results_2 = list(self.memory_saver.list(None, filter=query_2))
        assert len(search_results_2) == 1
        assert search_results_2[0].metadata == self.metadata_2

        search_results_3 = list(self.memory_saver.list(None, filter=query_3))
        assert len(search_results_3) == 3

        search_results_4 = list(self.memory_saver.list(None, filter=query_4))
        assert len(search_results_4) == 0

        # search by config (defaults to checkpoints across all namespaces)
        search_results_5 = list(
            self.memory_saver.list({"configurable": {"thread_id": "thread-2"}})
        )
        assert len(search_results_5) == 2
        assert {
            search_results_5[0].config["configurable"]["checkpoint_ns"],
            search_results_5[1].config["configurable"]["checkpoint_ns"],
        } == {"", "inner"}

        # TODO: test before and limit params

    @pytest.mark.skip(
        reason=(
            "AuroraDataApiSaver.alist deliberately does not implement metadata "
            "filtering (see its docstring and "
            "tests/test_aurora_dataapi_saver_writes.py::test_alist_raises_for_"
            "nonempty_filter) -- a documented, already-tested design limit, not "
            "a gap this task should close. See task-6-report.md."
        )
    )
    async def test_asearch(self) -> None:
        # set up test
        # save checkpoints
        await self.memory_saver.aput(
            self.config_1,
            self.chkpnt_1,
            self.metadata_1,
            self.chkpnt_1["channel_versions"],
        )
        await self.memory_saver.aput(
            self.config_2,
            self.chkpnt_2,
            self.metadata_2,
            self.chkpnt_2["channel_versions"],
        )
        await self.memory_saver.aput(
            self.config_3,
            self.chkpnt_3,
            self.metadata_3,
            self.chkpnt_3["channel_versions"],
        )

        # call method / assertions
        query_1 = {"source": "input"}  # search by 1 key
        query_2 = {
            "step": 1,
            "writes": {"foo": "bar"},
        }  # search by multiple keys
        query_3: dict[str, Any] = {}  # search by no keys, return all checkpoints
        query_4 = {"source": "update", "step": 1}  # no match

        search_results_1 = [
            c async for c in self.memory_saver.alist(None, filter=query_1)
        ]
        assert len(search_results_1) == 1
        assert search_results_1[0].metadata == self.metadata_1

        search_results_2 = [
            c async for c in self.memory_saver.alist(None, filter=query_2)
        ]
        assert len(search_results_2) == 1
        assert search_results_2[0].metadata == self.metadata_2

        search_results_3 = [
            c async for c in self.memory_saver.alist(None, filter=query_3)
        ]
        assert len(search_results_3) == 3

        search_results_4 = [
            c async for c in self.memory_saver.alist(None, filter=query_4)
        ]
        assert len(search_results_4) == 0


@pytest.mark.skip(
    reason=(
        "InMemorySaver's __enter__/__aenter__/__exit__/__aexit__ are its own "
        "feature, not part of BaseCheckpointSaver -- AuroraDataApiSaver does "
        "not implement the context manager protocol at all. See "
        "task-6-report.md."
    )
)
async def test_memory_saver() -> None:
    memory_saver = InMemorySaver()
    assert isinstance(memory_saver, InMemorySaver)

    async with memory_saver as async_memory_saver:
        assert async_memory_saver is memory_saver

    with memory_saver as sync_memory_saver:
        assert sync_memory_saver is memory_saver


async def test_memory_saver_warns_on_unregistered_msgpack(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Adapted: sync .put()/.get_tuple() -> async aput/aget_tuple; InMemorySaver
    # -> AuroraDataApiSaver(FakeCluster(), serde=serde). This exercises
    # JsonPlusSerializer's allowlist/warning behavior, which is generic to any
    # saver built on that serde, not an InMemorySaver-specific feature.
    serde = JsonPlusSerializer()
    memory_saver = AuroraDataApiSaver(FakeCluster(), serde=serde)
    obj = MemoryPydantic(foo="bar")

    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"foo": obj}
    checkpoint["channel_versions"] = {"foo": 1}

    config: RunnableConfig = {
        "configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}
    }

    caplog.set_level(logging.WARNING, logger="langgraph.checkpoint.serde.jsonplus")
    new_config = await memory_saver.aput(config, checkpoint, {}, {"foo": 1})
    result = await memory_saver.aget_tuple(new_config)

    assert result is not None
    assert "unregistered type" in caplog.text.lower()
    assert result.checkpoint["channel_values"]["foo"] == obj


async def test_memory_saver_allowlist_silences_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    serde = JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("tests.conformance.test_checkpointer_conformance", "MemoryPydantic")
        ]
    )
    memory_saver = AuroraDataApiSaver(FakeCluster(), serde=serde)
    obj = MemoryPydantic(foo="bar")

    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"foo": obj}
    checkpoint["channel_versions"] = {"foo": 1}

    config: RunnableConfig = {
        "configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}
    }

    caplog.set_level(logging.WARNING, logger="langgraph.checkpoint.serde.jsonplus")
    new_config = await memory_saver.aput(config, checkpoint, {}, {"foo": 1})
    result = await memory_saver.aget_tuple(new_config)

    assert result is not None
    assert "unregistered type" not in caplog.text.lower()
    assert result.checkpoint["channel_values"]["foo"] == obj


async def test_memory_saver_strict_blocks_unregistered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    serde = JsonPlusSerializer(allowed_msgpack_modules=None)
    memory_saver = AuroraDataApiSaver(FakeCluster(), serde=serde)
    obj = MemoryPydantic(foo="bar")

    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"foo": obj}
    checkpoint["channel_versions"] = {"foo": 1}

    config: RunnableConfig = {
        "configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}
    }

    caplog.set_level(logging.WARNING, logger="langgraph.checkpoint.serde.jsonplus")
    new_config = await memory_saver.aput(config, checkpoint, {}, {"foo": 1})
    result = await memory_saver.aget_tuple(new_config)

    assert result is not None
    assert "blocked" in caplog.text.lower()
    expected = obj.model_dump() if hasattr(obj, "model_dump") else obj.dict()
    assert result.checkpoint["channel_values"]["foo"] == expected


async def test_memory_saver_with_allowlist_proxy_isolated() -> None:
    serde = JsonPlusSerializer(allowed_msgpack_modules=None)
    memory_saver = AuroraDataApiSaver(FakeCluster(), serde=serde)
    proxy = memory_saver.with_allowlist(
        [("tests.conformance.test_checkpointer_conformance", "MemoryPydantic")]
    )

    obj = MemoryPydantic(foo="bar")

    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"foo": obj}
    checkpoint["channel_versions"] = {"foo": 1}

    config: RunnableConfig = {
        "configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}
    }

    new_config = await proxy.aput(config, checkpoint, {}, {"foo": 1})

    proxied = await proxy.aget_tuple(new_config)
    assert proxied is not None
    assert proxied.checkpoint["channel_values"]["foo"] == obj

    direct = await memory_saver.aget_tuple(new_config)
    assert direct is not None
    expected = obj.model_dump() if hasattr(obj, "model_dump") else obj.dict()
    assert direct.checkpoint["channel_values"]["foo"] == expected


@pytest.mark.skip(
    reason=(
        "Tests InMemorySaver's own private _load_blobs/.blobs override of "
        "get_delta_channel_history, not the BaseCheckpointSaver contract. "
        "AuroraDataApiSaver never overrides get_delta_channel_history, so it "
        "has no equivalent private state to exercise here. See "
        "task-6-report.md."
    )
)
class TestInMemorySaverDeltaChannel:
    def test_load_blobs_omits_delta_channel(self) -> None:
        """_load_blobs omits delta channels (stored as 'empty'); reconstruction deferred."""
        saver = InMemorySaver()

        thread_id, ns, channel = "t1", "", "messages"
        v1 = "00000000000000000000000000000001.0000000000000000"

        saver.blobs[(thread_id, ns, channel, v1)] = ("empty", b"")

        result = saver._load_blobs(thread_id, ns, {channel: v1})
        assert channel not in result

    def test_get_channel_writes_collects_ancestor_writes_only(self) -> None:
        """`get_delta_channel_history` collects ancestor writes oldest→newest, and
        excludes writes stored at the target checkpoint itself (those are
        pending writes for the next step, applied separately by pregel).

        When the walk reaches the root without finding any stored value for
        the channel, the per-channel entry has no `seed` key (TypedDict
        absence indicates "start empty").
        """
        saver = InMemorySaver()
        serde = JsonPlusSerializer()

        thread_id, ns, channel = "t1", "", "messages"

        cp1 = empty_checkpoint()
        cp1["id"] = "cp1"
        cp2 = empty_checkpoint()
        cp2["id"] = "cp2"
        saver.storage[thread_id][ns] = {
            "cp1": (serde.dumps_typed(cp1), serde.dumps_typed({}), None),
            "cp2": (serde.dumps_typed(cp2), serde.dumps_typed({}), "cp1"),
        }
        # Writes stored at cp1 produced the cp1 snapshot; part of history.
        saver.writes[(thread_id, ns, "cp1")][("task1", 0)] = (
            "task1",
            channel,
            serde.dumps_typed({"content": "hi"}),
            "",
        )
        # Writes stored at cp2 are pending — they will produce cp3 when the
        # step that loaded cp2 completes. They MUST NOT appear in the
        # reconstructed snapshot value at cp2.
        saver.writes[(thread_id, ns, "cp2")][("task2", 0)] = (
            "task2",
            channel,
            serde.dumps_typed({"content": "pending"}),
            "",
        )

        config: RunnableConfig = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "checkpoint_id": "cp2",
            }
        }
        result = saver.get_delta_channel_history(config=config, channels=[channel])[
            channel
        ]
        # Walk reached the root without finding a stored value → no `seed` key.
        assert "seed" not in result
        values = [v for _, _, v in result["writes"]]
        assert values == [{"content": "hi"}]

    def test_get_channel_writes_at_root_returns_empty(self) -> None:
        """Reconstructing the root checkpoint's state: no ancestors → []."""
        saver = InMemorySaver()
        serde = JsonPlusSerializer()
        thread_id, ns, channel = "t1", "", "messages"

        cp1 = empty_checkpoint()
        cp1["id"] = "cp1"
        saver.storage[thread_id][ns] = {
            "cp1": (serde.dumps_typed(cp1), serde.dumps_typed({}), None),
        }
        saver.writes[(thread_id, ns, "cp1")][("task1", 0)] = (
            "task1",
            channel,
            serde.dumps_typed({"content": "pending"}),
            "",
        )

        config: RunnableConfig = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "checkpoint_id": "cp1",
            }
        }
        result = saver.get_delta_channel_history(config=config, channels=[channel])[
            channel
        ]
        # No ancestors → no seed found, no writes accumulated.
        assert "seed" not in result
        assert result["writes"] == []


class TestBaseFallbackGetChannelWrites:
    """Exercises the `BaseCheckpointSaver.get_delta_channel_history` default
    implementation — the path third-party savers inherit when they don't
    override `get_delta_channel_history` themselves. `AuroraDataApiSaver`
    never overrides it, so this class runs against the real saver.

    Regression guard for a bug where the fallback passed the caller's config
    (with `checkpoint_id`) straight to `self.list()`, which most savers
    collapse to a single row — causing the fallback to return `[]`.
    """

    async def _build_saver_with_chain(self) -> tuple[AuroraDataApiSaver, str, str]:
        """Build an AuroraDataApiSaver with a 3-checkpoint chain and per-step
        writes for a `messages` channel.

        Adapted: upstream builds this by poking InMemorySaver's private
        `.storage`/`.writes` dicts directly. Here the same chain is built
        through the saver's own public `aput`/`aput_writes`, which is more
        portable and exercises the real write path this test's target
        (the base-class fallback) actually consumes through `aget_tuple`.

        Returns `(saver, thread_id, namespace)`.
        """
        saver = AuroraDataApiSaver(FakeCluster())
        thread_id, ns = "t1", ""

        cp0_id = "00000000000000000000000000000001.0000000000000000"
        cp1_id = "00000000000000000000000000000002.0000000000000000"
        cp2_id = "00000000000000000000000000000003.0000000000000000"

        cp0 = empty_checkpoint()
        cp0["id"] = cp0_id
        await saver.aput(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns}},
            cp0,
            {},
            {},
        )
        # Writes under cp0 produced cp1's state; writes under cp1 produced cp2's.
        await saver.aput_writes(
            {
                "configurable": {
                    "thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": cp0_id
                }
            },
            [("messages", "first")],
            "task1",
        )

        cp1 = empty_checkpoint()
        cp1["id"] = cp1_id
        await saver.aput(
            {
                "configurable": {
                    "thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": cp0_id
                }
            },
            cp1,
            {},
            {},
        )
        await saver.aput_writes(
            {
                "configurable": {
                    "thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": cp1_id
                }
            },
            [("messages", "second")],
            "task2",
        )

        cp2 = empty_checkpoint()
        cp2["id"] = cp2_id
        await saver.aput(
            {
                "configurable": {
                    "thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": cp1_id
                }
            },
            cp2,
            {},
            {},
        )
        return saver, thread_id, ns

    @pytest.mark.skip(
        reason=(
            "AuroraDataApiSaver has no sync get_tuple (async-only by design), "
            "so the sync fallback path raises NotImplementedError immediately. "
            "Coverage for the fallback itself is provided by "
            "test_async_fallback_returns_ancestor_writes_oldest_first. See "
            "task-6-report.md."
        )
    )
    async def test_fallback_returns_ancestor_writes_oldest_first(self) -> None:
        saver, thread_id, ns = await self._build_saver_with_chain()
        target_id = "00000000000000000000000000000003.0000000000000000"
        config: RunnableConfig = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "checkpoint_id": target_id,
            }
        }

        result = saver.get_delta_channel_history(config=config, channels=["messages"])[
            "messages"
        ]

        # Walk reached root without a stored value → `seed` key absent.
        assert "seed" not in result
        values = [v for _, _, v in result["writes"]]
        assert values == [{"content": "first"}, {"content": "second"}]

    async def test_async_fallback_returns_ancestor_writes_oldest_first(self) -> None:
        saver, thread_id, ns = await self._build_saver_with_chain()
        target_id = "00000000000000000000000000000003.0000000000000000"
        config: RunnableConfig = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "checkpoint_id": target_id,
            }
        }

        result = (
            await saver.aget_delta_channel_history(config=config, channels=["messages"])
        )["messages"]

        assert "seed" not in result
        values = [v for _, _, v in result["writes"]]
        assert values == ["first", "second"]

    async def test_async_fallback_concurrent_tasks_do_not_interfere(self) -> None:
        """Regression: the re-entrancy guard must be task-local, not thread-local.

        Two concurrent `aget_delta_channel_history` calls on the same event-loop
        thread must each see their full reconstructed writes. A
        `threading.local()` guard would let whichever task set it first
        short-circuit the other to `writes=[]`.
        """
        import asyncio

        saver, thread_id, ns = await self._build_saver_with_chain()

        # Force the two tasks to interleave across the `set(True)` boundary:
        # each `aget_tuple` yields control, so if the guard were thread-local
        # the second task would observe `active=True` set by the first.
        orig_aget_tuple = saver.aget_tuple

        async def slow_aget_tuple(config: RunnableConfig) -> Any:
            await asyncio.sleep(0)
            return await orig_aget_tuple(config)

        saver.aget_tuple = slow_aget_tuple  # type: ignore[method-assign]

        target_id = "00000000000000000000000000000003.0000000000000000"
        config: RunnableConfig = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "checkpoint_id": target_id,
            }
        }

        results = await asyncio.gather(
            saver.aget_delta_channel_history(config=config, channels=["messages"]),
            saver.aget_delta_channel_history(config=config, channels=["messages"]),
        )

        expected_values = ["first", "second"]
        for result_map in results:
            result = result_map["messages"]
            assert "seed" not in result
            values = [v for _, _, v in result["writes"]]
            assert values == expected_values


@pytest.mark.skip(
    reason=(
        "Tests InMemorySaver's own pre-delta-blob-terminator override of "
        "get_delta_channel_history (distinguishing a real blob from an "
        "'empty' sentinel), not the BaseCheckpointSaver contract. "
        "AuroraDataApiSaver never overrides get_delta_channel_history, so its "
        "inherited fallback has no pre-delta/delta distinction to terminate "
        "on. See task-6-report.md."
    )
)
class TestPreDeltaBlobTerminator:
    """Verify the pre-delta blob terminator: when the ancestor walk hits a
    checkpoint whose blob for the channel is a real value, reconstruction
    seeds from it and stops. This guards

      * back-compat: a thread written by pre-delta code, then extended under
        delta — reconstruction must return the correct value without walking
        past the last pre-delta ancestor;
      * perf: without the terminator, every reconstruct-after-migration would
        walk all the way to the thread root.

    Under the new public API a found seed populates `seed` in the
    `DeltaChannelHistory` TypedDict; absence of the `seed` key means the walk
    reached root without finding a stored value.
    """

    def _build_mixed_thread(self) -> tuple[InMemorySaver, str, str, str, str]:
        """Three-checkpoint chain: cp1 (pre-delta, blob=[A]), cp2 (delta,
        write=B), cp3 (delta, write=C). Reconstructing at cp3 must yield
        seed=[A] + writes=[B, C].

        Returns `(saver, thread_id, ns, channel, cp3_id)`.
        """
        saver = InMemorySaver()
        serde = JsonPlusSerializer()
        thread_id, ns, channel = "t1", "", "messages"

        v1 = "00000000000000000000000000000001.0"
        v2 = "00000000000000000000000000000002.0"
        v3 = "00000000000000000000000000000003.0"

        # Pre-delta: cp1 stored a real blob for the channel.
        saver.blobs[(thread_id, ns, channel, v1)] = serde.dumps_typed(["A"])
        # Delta-era: cp2 and cp3 store "empty"; real writes in checkpoint_writes.
        saver.blobs[(thread_id, ns, channel, v2)] = ("empty", b"")
        saver.blobs[(thread_id, ns, channel, v3)] = ("empty", b"")

        cp1 = empty_checkpoint()
        cp1["id"] = "cp1"
        cp1["channel_versions"][channel] = v1
        cp2 = empty_checkpoint()
        cp2["id"] = "cp2"
        cp2["channel_versions"][channel] = v2
        cp3 = empty_checkpoint()
        cp3["id"] = "cp3"
        cp3["channel_versions"][channel] = v3

        saver.storage[thread_id][ns] = {
            "cp1": (serde.dumps_typed(cp1), serde.dumps_typed({}), None),
            "cp2": (serde.dumps_typed(cp2), serde.dumps_typed({}), "cp1"),
            "cp3": (serde.dumps_typed(cp3), serde.dumps_typed({}), "cp2"),
        }
        # Write under cp1 would be from the pre-delta era and MUST be ignored
        # (the blob already captures it). We add one and assert it is not
        # folded into the reconstructed result.
        saver.writes[(thread_id, ns, "cp1")][("task0", 0)] = (
            "task0",
            channel,
            serde.dumps_typed("PRE-DELTA-WRITE"),
            "",
        )
        saver.writes[(thread_id, ns, "cp2")][("task2", 0)] = (
            "task2",
            channel,
            serde.dumps_typed("B"),
            "",
        )
        saver.writes[(thread_id, ns, "cp3")][("task3", 0)] = (
            "task3",
            channel,
            serde.dumps_typed("PENDING-AT-TARGET"),
            "",
        )
        return saver, thread_id, ns, channel, "cp3"

    def test_seed_from_pre_delta_ancestor_blob(self) -> None:
        saver, thread_id, ns, channel, target = self._build_mixed_thread()
        config: RunnableConfig = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "checkpoint_id": target,
            }
        }

        result = saver.get_delta_channel_history(config=config, channels=[channel])[
            channel
        ]

        # Seed came from the pre-delta blob at cp1.
        assert result["seed"] == ["A"]
        # Delta-era writes from cp2 replay through the reducer on top of seed.
        # cp3 is the target — its own write is pending for the NEXT step and
        # must be excluded.
        values = [v for _, _, v in result["writes"]]
        assert values == ["B"]

    def test_pre_delta_blob_terminates_walk_before_older_writes(self) -> None:
        """Writes stored at the pre-delta ancestor itself must not be replayed
        (the blob subsumes them)."""
        saver, thread_id, ns, channel, target = self._build_mixed_thread()
        config: RunnableConfig = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": ns,
                "checkpoint_id": target,
            }
        }

        result = saver.get_delta_channel_history(config=config, channels=[channel])[
            channel
        ]

        values = [v for _, _, v in result["writes"]]
        # The pre-delta write under cp1 must not appear (the blob subsumes it).
        assert "PRE-DELTA-WRITE" not in values
        # And the pending write at the target is never folded in.
        assert "PENDING-AT-TARGET" not in values
