# 05 - Workflow

Resume execution without duplicating the business action.

[All five capabilities](../README.md) | [Talk run of show](../../../docs/TALK_RUN_OF_SHOW.md)

## Open this code

| Source | Role |
| --- | --- |
| [`graph.py`](graph.py) | Builds the Strands Graph, its edges and the review gate that pauses it, and folds saved snapshots back into workflow state. |
| [`runner.py`](runner.py) | Claims the execution lease, runs or resumes the graph, keeps the lease alive, and reports the outcome. |
| [`lease.py`](lease.py) | Binds a thread to its journey and claims, renews and releases the execution lease in Aurora. |
| [`nodes.py`](nodes.py) | The steps the graph runs: classify, search, availability, memory recall, prepare hold, hold and synthesize. |
| [`snapshot_storage.py`](snapshot_storage.py) | Appends each Strands snapshot to Aurora as a JSONB row, only while the writing execution still holds the thread. |
| [`service.py`](service.py) | Builds the runner the app, the scripts and the AgentCore Runtime use. |
| [`state.py`](state.py) | Defines the workflow state, the trace span shape and the stable hold identifier. |
| [`routing.py`](routing.py) | Decides the intent of a request, whether it is a recovery and whether it pauses after search. |
| [`hold_intent.py`](hold_intent.py) | Fixes the identity and terms of a hold before it runs so every retry reuses them. |
| [`governed_hold.py`](governed_hold.py) | Places the hold through the AgentCore Gateway so Cedar policy decides it. |
| [`packages.py`](packages.py) | Reads the ranked package and an available duration out of workflow state. |
| [`memory_recall.py`](memory_recall.py) | Reads saved preferences, session turns and similar trips from Aurora, then searches the catalog. |

## Live checkpoint

> My JFK-to-Tokyo flight was canceled. Rework the trip, then check duration availability for the best three options.

Continue at Recovery desk, restart the backend, then resume. System evidence should show the same thread and an actual persisted hold. The lost-response exercise additionally verifies the same booking ID and original expiry.

## Architectural takeaway

A checkpoint and a business write are separate transactions. Combine resumable execution with idempotent actions and authoritative readback.

## Transition

Close in System evidence, then use Solution briefing for questions about the architecture.
