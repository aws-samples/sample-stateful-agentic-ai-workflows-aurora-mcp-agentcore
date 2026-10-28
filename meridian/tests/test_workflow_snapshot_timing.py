"""Each snapshot span carries the time of the write that saved its own node."""

import json

from strands.storage import InMemoryStorage

from backend.agents.phase_05_workflow import runner as runner_module
from tests.test_workflow_runner import RECOVERY, World, command

DETAILS_PREFIX = "Workflow snapshot appended after the "


class SizedWrites:
    """Storage that reports 100 x the nodes a snapshot has completed as the write's duration."""

    def __init__(self, inner, on_write):
        self._inner, self._on_write = inner, on_write

    async def write(self, key, data):
        await self._inner.write(key, data)
        completed = json.loads(data)["data"]["state"].get("execution_order") or []
        self._on_write(100 * len(completed))

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _sized_runner(world):
    runner = world.runner()

    def storage_for(thread_id, traveler_id, execution_id, worker_id, on_write):
        return SizedWrites(world.storages.setdefault(thread_id, InMemoryStorage()), on_write)

    runner._storage_for = storage_for
    return runner


def _times_by_node(activities):
    return {
        span["details"].removeprefix(DETAILS_PREFIX).removesuffix(" node"):
            span["execution_time_ms"]
        for span in activities
        if span["title"].startswith(runner_module.SNAPSHOT_PREFIX)
        and span["execution_time_ms"] is not None
    }


async def test_a_resume_that_records_a_choice_pairs_each_write_with_its_own_node():
    world = World()
    await _sized_runner(world).run(command(RECOVERY))
    resumed = await _sized_runner(world).run(
        command(RECOVERY, resume=True, selected_package_id="pkg_tokyo")
    )
    saved = json.loads(await world.snapshot("t1"))["data"]["state"]["execution_order"]
    times = _times_by_node(resumed["activities"])
    assert times, "the resumed run reported no snapshot spans"
    for node, took in times.items():
        assert took == 100 * (saved.index(node) + 1), (node, took, saved)


def _span(title="Snapshot saved: AuroraSnapshotStorage.write"):
    return {"title": title, "execution_time_ms": None}


def _two_nodes(monkeypatch):
    monkeypatch.setattr(
        runner_module, "node_spans",
        lambda _state: [("search", [_span()]), ("hold", [_span()])],
    )


def _times(timed):
    return [span["execution_time_ms"] for span in timed]


def test_writes_pair_with_the_nodes_in_order_and_a_trailing_write_is_ignored(monkeypatch):
    _two_nodes(monkeypatch)
    assert _times(runner_module._timed(object(), set(), [11, 22, 33])) == [11, 22]


def test_fewer_writes_than_nodes_attaches_no_time_rather_than_a_wrong_one(monkeypatch):
    _two_nodes(monkeypatch)
    assert _times(runner_module._timed(object(), set(), [11])) == [None, None]


def test_a_node_saved_by_an_earlier_invocation_gets_no_time_and_takes_no_write(monkeypatch):
    _two_nodes(monkeypatch)
    assert _times(runner_module._timed(object(), {"search"}, [22, 33])) == [None, 22]


def test_exactly_one_write_per_node_pairs_every_node(monkeypatch):
    _two_nodes(monkeypatch)
    assert _times(runner_module._timed(object(), set(), [11, 22])) == [11, 22]


def test_an_empty_run_attaches_no_time_and_raises_nothing(monkeypatch):
    monkeypatch.setattr(runner_module, "node_spans", lambda _state: [])
    assert runner_module._timed(object(), set(), []) == []
    assert runner_module._timed(object(), set(), [11]) == []
