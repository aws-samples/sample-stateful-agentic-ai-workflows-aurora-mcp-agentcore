# Meridian code walkthrough

A guided tour of the source, in the order the application builds up its
capabilities. Paths are relative to `meridian/`. Search for the named symbols
rather than line numbers, which change.

The entry point for every chat turn is `backend/routers/chat.py`. The Strands
classes in `backend/agents/sql_01/` and `backend/agents/mcp_02/` are reference
implementations; the first two phases run the same steps directly in
`chat.py`.

## Three kinds of state

| State | Source | What to look for |
| --- | --- | --- |
| Traveler context | `backend/agents/production_04/concierge.py`, `process_turn` | Authorized Aurora facts and the runtime's managed session inform a turn |
| Workflow progress | `backend/agents/orchestration_05/workflow.py`, `initialize_checkpoint_backend` | A durable saver restores execution after the process is replaced |
| Business result | `scripts/migrations/008_hold_request_identity.sql` | A persisted intent and transaction constraints make a repeated write safe |

Remembering a sentence does not establish a booking, and a database connection
is not durable state.

## SQL

Prompt: `Show me city trips under $2,000 per traveler.`

Open `sql_search` in `backend/routers/chat.py` and follow `parse_search_query`
and `execute_keyword_search`. The trace shows the parsed filter, the
parameterized values, the returned rows and the elapsed time. This path filters
through the RDS Data API directly; it does not run the reference Strands SQL
agent.

## MCP

Prompt: `Compare three trip types and convert each price to euros.`

Open `mcp_search` and `_call_domain_tool` in `backend/routers/chat.py`, then
`compare_packages` and `currency_convert` in `backend/mcp/concierge_server.py`.
This prompt uses only the `meridian-concierge` server. Other catalog prompts
also call the PostgreSQL MCP server through `mcp_session` in
`backend/mcp/mcp_client.py`; the trace lists the servers that answered.
Currency conversion uses indicative rates.

## Retrieval

Prompt: `Find a quiet, romantic wine-country retreat with a private villa.`

Open `retrieval_supervisor_search` in `backend/routers/chat.py`, then
`SearchAgent.hybrid_search` in `backend/agents/retrieval_03/search_agent.py`:
query embedding, semantic and lexical candidates, deduplication by package ID,
and `rerank_documents`. The Bedrock adapters are in
`backend/db/embedding_service.py`. A relevant result does not establish
facts the catalog does not record, such as private occupancy.

## Identity, memory and policy

Turn on **Use traveler context** before the Production prompt
`Recall my Tokyo plan and saved preferences: home airport, food needs, and budget.`
With context off, the turn returns without calling the runtime; the guard is in
`chat` in `backend/routers/chat.py`.

| Source and symbol | What it does |
| --- | --- |
| `backend/http_auth.py`, `require_http_principal` | Binds the HTTP request to its permitted traveler. The sample uses a shared demo principal, not user authentication. |
| `backend/db/rds_data_client.py`, `scoped_session` | Checks the workload grant, sets a transaction-local scope and switches to the restricted RLS role |
| `backend/agents/production_04/concierge.py`, `process_turn` | Short authorization and read, and write and audit, units around the external runtime call |
| `meridian_agentcore/app/MeridianConcierge/main.py` | The Strands loop on AgentCore Runtime: Gateway tools, the Memory session manager and the streamed events |
| `meridian_agentcore/app/MeridianConcierge/turn_trace.py`, `TraceHooks._pin_arguments` | Overwrites the traveler, the confirmation flag, the budget ceiling and the journey reference with values from the authorized request before the Cedar policy evaluates a hold or confirmation. Party size is not pinned: it comes from the tool arguments, and the hold policy caps it at 6 travelers. |
| `meridian_agentcore/agentcore/agentcore.template.json`, `MeridianGovernance` | The Cedar read, hold and confirmation policies, in `ENFORCE` mode |
| `backend/routers/diagnostics.py` | The live allow and deny checks, restricted-role row counts and RLS policy evidence |

An IAM or traveler-grant denial is not a Cedar decision; the trace reports each
at its own boundary.

## Checkpoints, workers and the hold

Prompt: `My JFK-to-Tokyo flight was canceled. Rework the trip, then check duration availability for the best three options.`

The recovery path has six nodes and pauses after `search`:

```text
classify → search → availability → prepare_hold → hold → synthesize
             pause                  durable intent   governed write
```

The graph also registers `memory_recall` for another branch; not every branch
visits every node.

| Source and symbol | What it does |
| --- | --- |
| `backend/agents/orchestration_05/workflow.py`, graph builder | Edges, the pause configuration and the saver passed to `compile` |
| `backend/agents/orchestration_05/execution.py`, `run_http_workflow` | Duplicate-start guards, the execution claim, heartbeat and lease |
| `backend/agents/orchestration_05/hold_intent.py`, `prepare_hold_node` | Normalizes the terms and saves stable request and booking IDs before the hold node |
| `backend/agents/orchestration_05/workflow.py`, `_node_hold` | Calls the Gateway with the saved intent and the current execution lease |
| `meridian_agentcore/agentcore/gateway_targets/meridian_holds/lambda_function.py`, `create_courtesy_hold` | Reauthorizes the workload, checks the lease and calls Aurora's idempotent write |
| `scripts/migrations/008_hold_request_identity.sql` | Replay protection in the same transaction as the business write |
| `backend/agents/orchestration_05/workflow.py`, `_node_synthesize` | Builds the closing status from saved state, including the hold outcome and expiry |
| `backend/db/aurora_dataapi_saver.py`, `AuroraDataApiSaver` | The LangGraph checkpointer over the RDS Data API |

A graceful restart, a hard kill and a lost response are different failures;
[OPERATIONS.md](OPERATIONS.md#exercise-recovery-failures) shows how to
exercise each. A checkpoint and a business write are not one distributed
transaction: the design gives resumable execution with an idempotent action,
not exactly-once execution.

## Evidence

System evidence reads the selected journey back from Aurora through
`backend/db/journey_store.py`: thread, checkpoint, executions, worker,
authorization, hold ID, amount and expiry. A failed readback is reported as
unavailable evidence, not as a new outcome.

Related source:

- `frontend/src/api/request.ts`: bounded browser waits, which do not cancel transactions
- `frontend/src/showcase/lib/bookingRecovery.ts`: persisted retry references; the receipt itself comes from Aurora
- `backend/routers/chat.py`, `read_hold` and `read_booking`: authenticated reconciliation reads
- `scripts/migrations/010_confirm_booking.sql`: confirmation of an unexpired catalog hold, with no supplier booking or payment
- `scripts/lost_response_demo.py` and `scripts/kill_and_resume_demo.py`: the two failure exercises and their cleanup
