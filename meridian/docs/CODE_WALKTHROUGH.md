# Meridian code walkthrough cue sheet

Pair this with [DEMO_SCRIPT.md](../DEMO_SCRIPT.md) and the
[presenter guide](PRESENTER_GUIDE.md). Budget 40 minutes for slides, code, and
demo within the 60-minute session. All paths below are relative to `meridian/`.
Search for named symbols rather than relying on line numbers that move.

The live entry point is `backend/routers/chat.py`. The Strands classes in
`backend/agents/sql_01/` and `backend/agents/mcp_02/` are reference implementations,
not the code executed by the first two showcase examples.

## 0-4 minutes: three kinds of state

Open Concierge and the returned traveler profile. Alex's October Tokyo plan is
a fictional scenario; package inventory is not an airline feed. Use Solution
briefing's focused diagrams alongside the source.

| State | Source | Point to make |
| --- | --- | --- |
| Traveler context | `backend/agents/production_04/concierge.py`, `process_turn` | Authorized Aurora facts and managed conversation context inform a turn. |
| Workflow progress | `backend/agents/orchestration_05/workflow.py`, `initialize_checkpoint_backend` | A durable saver restores execution after process replacement. |
| Business result | `scripts/migrations/008_hold_request_identity.sql` | A persisted intent and transaction constraints make a repeated write safe. |

Remembering a sentence does not establish a booking. A connection is not durable state.

## 4-12 minutes: SQL, MCP, retrieval

### SQL: show the path that ran

Run **Show me city trips under $2,000 per traveler.** Open `sql_search` in
`backend/routers/chat.py`; follow `parse_search_query` and `execute_keyword_search`
to their imported implementation. Show the parsed filter, parameterized values,
returned rows and recorded time. This path performs direct RDS Data API filtering;
it does not invoke the reference Strands SQL agent. SQL can express richer
business logic; the limitation here is the sample's chosen filter interface.

### MCP: identify the observed server

Run **Compare three trip types and convert each price to euros.** Open
`mcp_search` and `_call_domain_tool` in `backend/routers/chat.py`, then
`compare_packages` and `currency_convert` in `backend/mcp/concierge_server.py`.

This pure-domain prompt uses `meridian-concierge` and skips the generic PostgreSQL
MCP query. Other catalog prompts use `mcp_session` from
`backend/mcp/mcp_client.py`; show that branch only if its trace was observed.
Do not describe two servers as having run when the trace contains one.
The protocol supplies a reusable tool contract. SQL could implement the same
operations. Currency conversion uses indicative rates, not settlement prices.

### Retrieval: candidates before ranking

Run **Find a quiet, romantic wine-country retreat with a private villa.** Open
`retrieval_supervisor_search` in `backend/routers/chat.py`, then
`SearchAgent.hybrid_search` in `backend/agents/retrieval_03/search_agent.py`.
Show query embedding, semantic and lexical candidates, deduplication, and
`rerank_documents`. The adapters are in `backend/db/embedding_service.py`.

Use the models and ordering observed in this run. A relevant villa result does
not establish private occupancy, dietary suitability, or a reservation unless
those facts are explicitly recorded. Inspect any missing requested attribute as
a gap. At minute 12, move to the authorization boundary.

## 12-20 minutes: identity, memory, policy

Enable **Use traveler context** and wait for **On** with the authorized profile
before submitting the recall prompt. Context off intentionally returns without
running the managed turn; the guard is in `chat` in `backend/routers/chat.py`.

| Source and symbol | What to show |
| --- | --- |
| `backend/http_auth.py`, `require_http_principal` | Bind the HTTP request to its permitted traveler. The sample uses a shared demo principal, not human-user authentication. |
| `backend/db/rds_data_client.py`, `scoped_session` | Check the workload grant, set transaction-local scope and enter the restricted RLS role. |
| `backend/agents/production_04/concierge.py`, `process_turn` | Short authorization/read and write units surround the external managed call. |
| `meridian_agentcore/app/MeridianConcierge/main.py` | Deployed Strands loop, Gateway tools, Memory session manager, and returned events. |
| `meridian_agentcore/app/MeridianConcierge/turn_trace.py`, `_pin_hold_arguments` | Pin traveler, approval, party, budget and journey from the authorized request before policy evaluates them. |
| `meridian_agentcore/agentcore/agentcore.json`, `MeridianGovernance` | Cedar read, hold and confirmation permits; verify deployed `ENFORCE` mode. |
| `backend/routers/diagnostics.py` | Actual allow/deny controls, restricted-role counts, and RLS policy evidence. |

Show one real refusal. An IAM or traveler-grant denial is not a Cedar decision.
The positive governed write follows in recovery. Save the separate 12-hour hold
for Q&A.

## 20-32 minutes: checkpoint, replacement worker, same intent

Run the canceled-flight prompt to its pause and keep the exact thread URL.
The canonical recovery path has six nodes:

```text
classify -> search -> availability -> prepare_hold -> hold -> synthesize
             pause                   durable intent   governed write
```

The graph also registers `memory_recall` for another branch. It is not a
five-node graph, and not every branch visits every node.

| Source and symbol | What to show |
| --- | --- |
| `backend/agents/orchestration_05/workflow.py`, graph builder | Edges, pause configuration, and saver passed to `compile`. |
| `backend/agents/orchestration_05/execution.py`, `run_http_workflow` | Duplicate-start guards, execution claim, heartbeat, and lease. |
| `backend/agents/orchestration_05/hold_intent.py`, `prepare_hold_node` | Normalize terms and save stable request/booking identity before the hold node. |
| `backend/agents/orchestration_05/workflow.py`, `_node_hold` | Invoke Gateway with the saved intent and current execution lease. |
| `meridian_agentcore/agentcore/gateway_targets/meridian_holds/lambda_function.py`, `create_courtesy_hold` | Reauthorize the workload and call Aurora's idempotent write. |
| `scripts/migrations/008_hold_request_identity.sql` | Replay protection belongs in the same transaction as the business write. |
| `backend/agents/orchestration_05/workflow.py`, `_node_synthesize` | Closing status comes from saved state, including hold outcome and expiry, without another model rewrite. |

Restart only the rehearsal backend, re-read the same thread, and select
**Resume and request hold**. Inspect the replacement worker and one recorded
15-minute hold. A graceful restart, a hard kill, and a lost response are distinct
tests; use separately retained fault-injection evidence for the latter two.
A checkpoint and business write are not one distributed transaction: the claim
is resumable execution with an idempotent action, not universal exactly-once execution.

## 32-40 minutes: evidence and takeaways

In System evidence, connect each claim to its record: thread, checkpoint,
execution, worker, authorization, hold ID, amount and expiry. The reader is
`backend/db/journey_store.py`; failed readback is not evidence of a new outcome.
Close with the context/progress/business-state distinction.

Optional source for discussion:

- `frontend/src/api/request.ts`: bounded browser waits, not transaction cancellation.
- `frontend/src/showcase/lib/bookingRecovery.ts`: persisted retry references, not receipt truth.
- `backend/routers/chat.py`, `read_hold` and `read_booking`: authenticated reconciliation.
- `scripts/migrations/010_confirm_booking.sql`: confirmation of an unexpired catalog hold, without supplier booking or payment.
- `scripts/lost_response_demo.py` and `scripts/kill_and_resume_demo.py`: distinct failure schedules and isolated-fixture cleanup.

If running late, cut extra searches and confirmations first. Preserve the
checkpoint, replacement worker, governed write, and database receipt.
