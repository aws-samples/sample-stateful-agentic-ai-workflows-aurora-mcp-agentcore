"""Backend selection and honest labelling.

The trace must name the backend that actually ran. A name-prefix test made a
durable Data API saver render as in-process, which is precisely the claim the
demo cannot afford to get wrong.
"""

from __future__ import annotations

import asyncio
import os

import pytest

import backend.agents.orchestration_05.workflow as workflow_mod
import backend.db.rds_data_client as rds_data_client
from backend.agents.orchestration_05.workflow import (
    CheckpointBackend,
    OrchestrationAgent,
    initialize_checkpoint_backend,
)


async def _noop(*args, **kwargs):
    return []


@pytest.fixture
def workflow() -> OrchestrationAgent:
    return OrchestrationAgent(search_fn=_noop, availability_fn=_noop)


def _break_the_cluster_arn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the Data API at a cluster that does not exist."""
    monkeypatch.setenv(
        "AURORA_CLUSTER_ARN",
        "arn:aws:rds:us-east-1:000000000000:cluster:no-such-cluster",
    )
    monkeypatch.setattr(rds_data_client, "_client", None)


@pytest.fixture(autouse=True)
def _isolated_backend(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("LANGGRAPH_CHECKPOINT_DSN", raising=False)
    monkeypatch.setenv("LANGGRAPH_AUTO_CHECKPOINT_DSN", "false")
    monkeypatch.setenv("LANGGRAPH_CHECKPOINT_REQUIRED", "false")
    monkeypatch.delenv("LANGGRAPH_CHECKPOINT_DATA_API", raising=False)
    workflow_mod._checkpoint_backend = None
    workflow_mod._checkpoint_init_lock = None
    rds_data_client._client = None
    yield
    workflow_mod._checkpoint_backend = None
    workflow_mod._checkpoint_init_lock = None
    rds_data_client._client = None


# --------------------------------------------------------------- labelling


@pytest.mark.parametrize(
    "kind,durable",
    [
        ("PostgresSaver (Aurora · pooled)", True),
        ("AuroraDataApiSaver", True),
        ("MemorySaver (in-process)", False),
    ],
)
def test_durability_comes_from_the_flag_not_the_name(
    workflow: OrchestrationAgent, kind: str, durable: bool
) -> None:
    workflow.checkpointer_kind = kind
    workflow.checkpointer_durable = durable
    assert workflow._uses_durable_saver is durable


def test_data_api_saver_is_not_labelled_in_process(
    workflow: OrchestrationAgent,
) -> None:
    workflow.checkpointer_kind = "AuroraDataApiSaver"
    workflow.checkpointer_durable = True
    activity = workflow._checkpoint_activity("search", 12)
    text = str(activity)
    assert "MemorySaver" not in text
    assert "AuroraDataApiSaver" in text


def test_memory_saver_is_still_labelled_in_process(
    workflow: OrchestrationAgent,
) -> None:
    workflow.checkpointer_kind = "MemorySaver (in-process)"
    workflow.checkpointer_durable = False
    assert "MemorySaver" in str(workflow._checkpoint_activity("search", 12))


def test_backend_carries_a_durable_flag() -> None:
    backend = CheckpointBackend(saver=object(), kind="AuroraDataApiSaver", durable=True)
    assert backend.durable is True


# ------------------------------------------------------------ data api gate


def test_data_api_backend_is_off_unless_asked_for() -> None:
    """Credentials in the environment must not silently redirect checkpoints.

    `RDSDataClient()` construction never validates its ARNs, so a bare
    try/except would adopt the Data API saver in every environment that has
    a dotenv loaded, including unit tests.
    """
    backend = asyncio.run(initialize_checkpoint_backend())
    assert backend.kind == "MemorySaver (in-process)"
    assert backend.durable is False


@pytest.mark.database
def test_the_data_api_backend_probes_the_real_cluster_and_is_adopted() -> None:
    """The probe has to reach Aurora, not a stand-in for it.

    A stubbed probe proves only that the branch is wired. It cannot tell you
    whether the checkpoint tables exist or the credentials work, which is the
    entire question the probe was added to answer.
    """
    os.environ["LANGGRAPH_CHECKPOINT_DATA_API"] = "true"
    try:
        backend = asyncio.run(initialize_checkpoint_backend())
    finally:
        del os.environ["LANGGRAPH_CHECKPOINT_DATA_API"]

    assert backend.kind == "AuroraDataApiSaver"
    assert backend.durable is True
    assert backend.error is None


@pytest.mark.database
def test_an_unreachable_cluster_falls_back_instead_of_failing_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cluster that does not answer must degrade, not break the first write.

    Pointed at an ARN that does not exist, so the Data API itself refuses.
    """
    monkeypatch.setenv("LANGGRAPH_CHECKPOINT_DATA_API", "true")
    _break_the_cluster_arn(monkeypatch)

    backend = asyncio.run(initialize_checkpoint_backend())
    assert backend.kind == "MemorySaver (in-process)"
    assert backend.durable is False
    assert "Data API checkpointing unavailable" in (backend.error or "")


@pytest.mark.database
def test_an_unreachable_cluster_raises_when_checkpoints_are_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGGRAPH_CHECKPOINT_DATA_API", "true")
    monkeypatch.setenv("LANGGRAPH_CHECKPOINT_REQUIRED", "true")
    _break_the_cluster_arn(monkeypatch)

    with pytest.raises(RuntimeError, match="Durable workflow checkpoints are required"):
        asyncio.run(initialize_checkpoint_backend())


# ----------------------------------------------------------- durability mode


def test_graph_invocation_requests_synchronous_durability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The recovery boundary must be committed before the turn returns.

    LangGraph 1.2.9 defaults durability to "async", which does not await the
    checkpoint write. A kill right after the interrupt would then land on a
    checkpoint that was never persisted.
    """
    wf = OrchestrationAgent(search_fn=_noop, availability_fn=_noop)
    seen: list[object] = []

    async def _spy(_self, _input, config=None, **kwargs):
        seen.append(kwargs.get("durability"))
        return {"activities": []}

    # Patch the compiled-graph class, not the instance: adopting a checkpoint
    # backend recompiles the graph and would discard an instance patch.
    monkeypatch.setattr(type(wf.graph), "ainvoke", _spy)

    asyncio.run(wf.run("Find me a Kyoto cultural trip", traveler_id="t1",
                       conversation_id="c-durability"))

    assert seen, "graph.ainvoke was never called"
    assert seen == ["sync"] * len(seen), (
        f'every invocation must pass durability="sync"; saw {seen}'
    )
