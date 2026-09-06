# Vendored checkpointer conformance suite

Source: https://github.com/langchain-ai/langgraph, `libs/checkpoint/tests/test_memory.py`
Tag: `checkpoint==4.1.1` (commit `d1e2ff0561a8b0b09212d0795c9d7b390a5de23a`,
"release(checkpoint): 4.1.1 (#7890)"). Vendored 2026-09-06.

The installed distribution (`langgraph-checkpoint==4.1.1`) ships only `base`,
`memory`, `postgres`, and `serde` -- no tests -- so this suite is vendored
from the upstream tag that matches the installed version exactly (confirmed
via `pip show langgraph-checkpoint` and the tag's own release commit
message). There is no `4.1.1` branch/tag on the `langgraph` monorepo itself;
the per-package release tags are named `checkpoint==X.Y.Z`, found via
`git ls-remote --tags` filtered on `checkpoint`.

Re-vendor whenever `langgraph-checkpoint` is upgraded: fetch the new tag,
diff `test_memory.py` against this file, and re-apply the adaptations below
(they do not survive a raw `cp`). Treat a failure here as a contract
violation in `AuroraDataApiSaver`, not as a flaky test, unless it falls into
one of the documented exceptions.

## Adaptations from upstream

The vendored file assumes `InMemorySaver`: an in-process saver with a
synchronous API, private dict-backed storage, and its own
`get_delta_channel_history` override. `AuroraDataApiSaver` is
Data-API-backed, async-only (by design -- every other test in this project
only exercises `aput`/`aget_tuple`/`aput_writes`/`alist`), and never
overrides `get_delta_channel_history`, so several tests needed adaptation
rather than a bare fixture swap:

- **Fixture**: `TestMemorySaver.setup` and the delta-channel test builders
  now construct `AuroraDataApiSaver(FakeCluster())` (optionally with a
  `serde=` override) instead of `InMemorySaver()`.
- **Sync -> async call sites**: tests that called `.put()`/`.get_tuple()`
  synchronously were converted to `await saver.aput(...)` /
  `await saver.aget_tuple(...)` inside `async def` tests. The assertions are
  unchanged.
- **Fixture setup via public API**: `TestBaseFallbackGetChannelWrites`'s
  chain builder wrote directly into `InMemorySaver.storage`/`.writes` dicts
  upstream; here it builds the same 3-checkpoint chain through
  `saver.aput`/`saver.aput_writes`, which is more portable and exercises the
  saver's real write path instead of a shortcut.
- **Skipped, with reasons on the test**: a few tests assume capabilities
  `AuroraDataApiSaver` deliberately does not have. Each carries a
  `@pytest.mark.skip(reason=...)` explaining which capability is missing and
  why it is out of scope for this saver, rather than being silently deleted.
  See `task-6-report.md` for the full list and reasoning.
