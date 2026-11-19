"""The conformance fixture clears only the thread ids this run generated."""

import asyncio

from examples.langgraph.tests.conformance import conftest as conformance
from examples.langgraph.tests.conformance import thread_ids

PROTECTED = ("thread-1", "thread-2", "thread-3", "t1")


class Recorder:
    def __init__(self):
        self.statements = []

    async def execute(self, sql, params=()):
        self.statements.append((sql, tuple(params)))
        return []


def test_the_cleanup_deletes_only_generated_ids():
    client = Recorder()

    asyncio.run(conformance._purge(client, thread_ids.GENERATED_THREADS))

    deleted = {params for _sql, params in client.statements}
    assert deleted == {(name,) for name in thread_ids.GENERATED_THREADS}
    assert not deleted & {(name,) for name in PROTECTED}
    assert {sql.split()[2] for sql, _ in client.statements} == {
        "checkpoint_writes", "checkpoint_blobs", "checkpoints"}


def test_the_fixture_passes_the_generated_ids_and_never_a_fixed_name():
    source = (conformance.__file__ and open(conformance.__file__, encoding="utf-8").read())

    assert "GENERATED_THREADS" in source
    assert not any(f'"{name}"' in source for name in PROTECTED)


def test_two_runs_and_two_workers_get_different_ids():
    first, second = thread_ids.new_run_token("gw0"), thread_ids.new_run_token("gw0")
    other_worker = thread_ids.new_run_token("gw1")

    assert len({first, second, other_worker}) == 3
    assert not set(thread_ids.thread_ids(first).values()) & set(
        thread_ids.thread_ids(second).values())
    assert first.endswith("-gw0") and other_worker.endswith("-gw1")


def test_no_generated_id_equals_an_upstream_name():
    assert not set(thread_ids.GENERATED_THREADS) & set(PROTECTED)
    assert all(name.startswith(thread_ids.RUN_TOKEN) for name in thread_ids.GENERATED_THREADS)


def test_the_suite_file_uses_the_generated_ids_not_literals():
    path = conformance.__file__.replace("conftest.py", "test_checkpointer_conformance.py")
    source = open(path, encoding="utf-8").read()

    assert not any(f'"{name}"' in source for name in thread_ids.UPSTREAM_NAMES)
