"""Every Phase 5 span title follows the Strands-era contract the showcase parses."""

import re

import pytest

from backend.activity import ActivityEntry, create_activity
from backend.agents.phase_05_workflow.runner import SNAPSHOT_PREFIX
from backend.agents.phase_05_workflow.state import SNAPSHOT_STORE
from backend.llm_polish import PolishResult
from backend.routers.chat import _is_workflow_resume_query, _polish_phase_reply
from tests.test_workflow_runner import World, command

TITLE = re.compile(
    r"^(Workflow node: [^·]+"
    r"|Snapshot saved: [^·]+"
    r"|Workflow paused at a saved step"
    r"|Workflow resumed from a saved step)$"
)


async def _paused_then_resumed_spans():
    world = World()
    paused = await world.runner("worker-a").run(command())
    resumed = await world.runner("worker-b").run(command("Resume workflow", resume=True))
    return paused["activities"], resumed["activities"]


async def test_every_span_title_is_in_the_contract_and_has_no_middle_dot():
    paused, resumed = await _paused_then_resumed_spans()
    titles = [span["title"] for span in paused + resumed]
    assert titles, "the run produced no spans"
    outside = [title for title in titles if not TITLE.match(title)]
    assert outside == []
    assert not any("·" in title for title in titles)


async def test_the_status_spans_say_saved_step_not_checkpoint():
    paused, resumed = await _paused_then_resumed_spans()
    assert paused[-1]["title"] == "Workflow paused at a saved step"
    assert resumed[-1]["title"] == "Workflow resumed from a saved step"


async def test_snapshot_spans_use_the_prefix_and_the_snapshot_fields():
    paused, _ = await _paused_then_resumed_spans()
    snapshots = [s for s in paused if s["title"].startswith(SNAPSHOT_PREFIX)]
    assert snapshots
    assert SNAPSHOT_PREFIX == "Snapshot saved: "
    for span in snapshots:
        fields = {f["label"]: f["value"] for f in span["telemetry"]["fields"]}
        assert fields["snapshot_durable"] == "true"
        assert "checkpoint_durable" not in fields
        assert fields["checkpointer"] == SNAPSHOT_STORE


async def test_status_spans_carry_the_snapshot_durable_field():
    paused, resumed = await _paused_then_resumed_spans()
    for span in (paused[-1], resumed[-1]):
        labels = [f["label"] for f in span["telemetry"]["fields"]]
        assert "snapshot_durable" in labels
        assert "checkpoint_durable" not in labels


@pytest.mark.parametrize(
    "prompt",
    [
        "Resume workflow from the saved step",
        "resume workflow from checkpoint",
        "Resume workflow from checkpoint",
        "continue from checkpoint",
        "continue from the saved step",
    ],
)
def test_the_resume_query_accepts_the_new_and_the_old_prompt(prompt):
    assert _is_workflow_resume_query(prompt)


def test_create_activity_takes_everything_after_the_type_by_keyword():
    with pytest.raises(TypeError):
        create_activity("search", "title")  # type: ignore[misc]
    assert create_activity("search", title="title").title == "title"


async def test_the_polish_context_lists_the_saved_step_status_spans(monkeypatch):
    seen = {}

    async def capture(user_query, raw):
        seen["raw"] = raw
        return PolishResult(text="ok", model_id=None, note="captured")

    monkeypatch.setattr("backend.routers.chat.polish_concierge_reply", capture)
    spans = [
        ActivityEntry(id=str(i), timestamp="2026-10-07T00:00:00Z", activity_type="result",
                      title=title)
        for i, title in enumerate(
            ["Workflow resumed from a saved step", "Snapshot saved: AuroraSnapshotStorage.write"]
        )
    ]
    await _polish_phase_reply(5, "resume", "Continued.", [], spans)
    assert "- Workflow resumed from a saved step" in seen["raw"]
    assert "- Snapshot saved: AuroraSnapshotStorage.write" in seen["raw"]
