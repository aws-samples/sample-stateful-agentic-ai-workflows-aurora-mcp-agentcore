# 04 - Production

Run a governed concierge for an authorized traveler.

[All five capabilities](../README.md) | [Talk run of show](../../../docs/TALK_RUN_OF_SHOW.md)

## Open this code

| Source | Role |
| --- | --- |
| [`concierge.py`](concierge.py): `process_turn` | Checks the workload grant, reads traveler context under RLS, calls the runtime, then persists the turn. |
| [`agentcore/runtime.py`](../../agentcore/runtime.py) | The managed Runtime adapter forwards real text deltas; the main Concierge exposes them through `/api/chat/stream`. |
| [`MeridianConcierge/main.py`](../../../meridian_agentcore/app/MeridianConcierge/main.py) | The deployed Strands loop, Gateway tools and AgentCore Memory. |
| [`agentcore.template.json`](../../../meridian_agentcore/agentcore/agentcore.template.json) | Gateway targets and enforced Cedar policies. |

## Live checkpoint

> Find Tokyo trips that fit my saved preferences.

Turn on Use traveler context in the ladder. Follow authorization, runtime and Gateway spans. For a hold, verify the policy decision and the stored receipt.

## Architectural takeaway

The model proposes. Trusted code pins traveler, confirmation and budget; Cedar governs the call, and Aurora enforces row access and records the result.

## Transition

A laptop or worker can disappear. The next capability makes multi-step work resumable.
