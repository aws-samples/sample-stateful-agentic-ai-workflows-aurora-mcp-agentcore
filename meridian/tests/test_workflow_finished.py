"""One rule decides that a workflow run is finished: its ``synthesize`` step completed."""

from backend.agents.phase_05_workflow.finished import run_finished, snapshot_finished


def _snapshot(completed, *, status="executing", next_nodes=()):
    return {"data": {"state": {
        "completed_nodes": list(completed), "status": status,
        "next_nodes_to_execute": list(next_nodes),
    }}}


def test_a_run_is_finished_when_synthesize_completed():
    assert run_finished(["classify", "search", "synthesize"])
    assert not run_finished(["classify", "search"])
    assert not run_finished([])


def test_a_snapshot_is_finished_whatever_its_status_says():
    assert snapshot_finished(_snapshot(["classify", "synthesize"], status="executing"))
    assert snapshot_finished(_snapshot(["classify", "synthesize"], status="completed"))


def test_a_paused_or_missing_snapshot_is_not_finished():
    assert not snapshot_finished(_snapshot(["classify", "search"], next_nodes=["synthesize"]))
    assert not snapshot_finished(None)
    assert not snapshot_finished({})
    assert not snapshot_finished({"data": {"state": {}}})
