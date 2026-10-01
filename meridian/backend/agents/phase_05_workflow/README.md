# 05 - Workflow

Resume execution without duplicating the business action.

[All five capabilities](../README.md) | [Talk run of show](../../../docs/TALK_RUN_OF_SHOW.md)

## Open this code

| Source | Role |
| --- | --- |
| [`workflow.py`](workflow.py) | LangGraph nodes, edges and the pause after search. |
| [`execution.py`](execution.py) | Worker claims, leases, heartbeat and duplicate-start protection. |
| [`hold_intent.py`](hold_intent.py), [`governed_hold.py`](governed_hold.py) | Durable action identity and a hold through the same governed Gateway. |
| [`db/aurora_dataapi_saver.py`](../../db/aurora_dataapi_saver.py) | Aurora checkpoints through the RDS Data API. |

## Live checkpoint

> My JFK-to-Tokyo flight was canceled. Rework the trip, then check duration availability for the best three options.

Continue at Recovery desk, restart the backend, then resume. System evidence should show the same thread and an actual persisted hold. The lost-response exercise additionally verifies the same booking ID and original expiry.

## Architectural takeaway

A checkpoint and a business write are separate transactions. Combine resumable execution with idempotent actions and authoritative readback.

## Transition

Close in System evidence, then use Solution briefing for questions about the architecture.
