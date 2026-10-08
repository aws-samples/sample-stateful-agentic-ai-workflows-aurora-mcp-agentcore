# Meridian code walkthrough

A guided tour of the source, in the order the application builds up its
capabilities. Paths are relative to `meridian/`. Search for the named symbols
rather than line numbers, which change.

The entry point for every chat turn is `backend/routers/chat.py`. The Strands
classes in `backend/agents/phase_01_sql/` and `backend/agents/phase_02_mcp/` are reference
implementations; the first two phases run the same steps directly in
`chat.py`.

## Three kinds of state

| State | Source | What to look for |
| --- | --- | --- |
| Traveler context | `backend/agents/phase_04_production/concierge.py`, `process_turn` | Authorized Aurora facts and the runtime's managed session inform a turn |
| Workflow progress | `backend/agents/phase_05_workflow/graph.py`, `snapshot_storage.py` | Saved snapshots restore execution after the Runtime session is replaced |
| Business result | `scripts/migrations/008_hold_request_identity.sql` | A persisted intent and transaction constraints make a repeated write safe |

Remembering a sentence does not establish a booking, and a database connection
is not durable state.

## 01 - SQL

Prompt: `Show me city trips under $2,000 per traveler.`

Open `sql_search` in `backend/routers/chat.py` and follow `parse_search_query`
and `execute_keyword_search`. The trace shows the parsed filter, the
parameterized values, the returned rows and the elapsed time. This path filters
through the RDS Data API directly; it does not run the reference Strands SQL
agent.

## 02 - MCP

Prompt: `Compare three trip types and convert each price to euros.`

Open `mcp_search` and `_call_domain_tool` in `backend/routers/chat.py`, then
`compare_packages` and `currency_convert` in `backend/mcp/concierge_server.py`.
This prompt uses only the `meridian-concierge` server. Other catalog prompts
also call the PostgreSQL MCP server through `mcp_session` in
`backend/mcp/mcp_client.py`; the trace lists the servers that answered.
Currency conversion uses indicative rates.

## 03 - Retrieval

Prompt: `Find a quiet, romantic wine-country retreat with a private villa.`

Open `retrieval_supervisor_search` in `backend/routers/chat.py`, then
`SearchAgent.hybrid_search` in `backend/agents/phase_03_retrieval/search_agent.py`:
query embedding, semantic and lexical candidates, deduplication by package ID,
and `rerank_documents`. The Bedrock adapters are in
`backend/db/embedding_service.py`. A relevant result does not establish
facts the catalog does not record, such as private occupancy.

## 04 - Production: identity, memory and policy

Turn on **Use traveler context** before the Production prompt
`Recall my Tokyo plan and saved preferences: home airport, food needs, and budget.`
With context off, the turn returns without calling the runtime; the guard is in
`chat` in `backend/routers/chat.py`.

| Source and symbol | What it does |
| --- | --- |
| `backend/http_auth.py`, `require_http_principal` | Binds the HTTP request to its permitted traveler. Until the identity release the default mode is `iam`; after it, the signed-in person's Cognito token is verified at each hop. |
| `backend/db/rds_data_client.py`, `scoped_session` | Checks the workload grant, sets a transaction-local scope and switches to the restricted RLS role |
| `backend/agents/phase_04_production/concierge.py`, `process_turn` | Short authorization and read, and write and audit, units around the external runtime call |
| `meridian_agentcore/app/MeridianConcierge/main.py` | The Strands loop on AgentCore Runtime: Gateway tools, the Memory session manager and the streamed events |
| `meridian_agentcore/app/MeridianConcierge/turn_trace.py`, `TraceHooks._pin_arguments` | Overwrites the traveler, the confirmation flag, the budget ceiling and the journey reference with values from the authorized request before the Cedar policy evaluates a hold or confirmation. Party size is not pinned: it comes from the tool arguments, and the hold policy caps it at 6 travelers. |
| `meridian_agentcore/agentcore/agentcore.template.json`, `MeridianGovernance` | The Cedar read, hold and confirmation policies, in `ENFORCE` mode |
| `backend/routers/diagnostics.py` | The live allow and deny checks, restricted-role row counts and RLS policy evidence |

An IAM or traveler-grant denial is not a Cedar decision; the trace reports each
at its own boundary.

## 05 - Workflow: snapshots, workers and the hold

Prompt: `My JFK-to-Tokyo flight was canceled. Rework the trip, then check duration availability for the best three options.`

The recovery path has six nodes and pauses after `search` for the traveler's review:

```text
classify → search → availability → prepare_hold → hold → synthesize
             pause                  durable intent   governed write
```

The graph also registers `memory_recall` for another branch; not every branch
visits every node.

| Source and symbol | What it does |
| --- | --- |
| `backend/agents/phase_05_workflow/graph.py`, `build_graph` | The Strands `GraphBuilder` edges, the review gate that pauses the run, and the session manager that saves a snapshot after every node |
| `backend/agents/phase_05_workflow/runner.py`, `WorkflowRunner.run` | Duplicate-start guards, the execution claim, heartbeat and lease |
| `backend/agents/phase_05_workflow/runtime_entry.py`, `workflow_turn` | The one event the `MeridianWorkflow` Runtime handles: runs the runner and streams heartbeats, then one result or error |
| `backend/agentcore/workflow_runtime.py`, `WorkflowRuntimeClient` | How the backend invokes the Runtime and stops its session |
| `backend/agents/phase_05_workflow/hold_intent.py`, `prepare_hold_node` | Normalizes the terms and saves stable request and booking IDs before the hold node |
| `backend/agents/phase_05_workflow/nodes.py`, `hold`, and `governed_hold.py` | Calls the Gateway with the saved intent and the current execution lease |
| `meridian_agentcore/agentcore/gateway_targets/meridian_holds/lambda_function.py`, `create_courtesy_hold` | Reauthorizes the workload, checks the lease and calls Aurora's idempotent write |
| `scripts/migrations/008_hold_request_identity.sql` | Replay protection in the same transaction as the business write |
| `backend/agents/phase_05_workflow/nodes.py`, `synthesize` | Builds the closing status from saved state, including the hold outcome and expiry |
| `backend/agents/phase_05_workflow/snapshot_storage.py`, `AuroraSnapshotStorage` | Appends each Strands snapshot as a JSONB row of `workflow_snapshots`, only while the writing execution still holds the thread |
| `backend/agents/phase_05_workflow/graph.py`, `ResumableStorage` | Repairs a snapshot saved after the traveler's answer so the Graph can resume |
| `backend/routers/journeys.py`, `stop_session` | Stops the journey's Runtime session and records it in `workflow_session_stops` |

A graceful restart, a hard kill and a lost response are different failures;
[OPERATIONS.md](OPERATIONS.md#exercise-recovery-failures) shows how to
exercise each. A snapshot and a business write are not one distributed
transaction: the design gives resumable execution with an idempotent action,
not exactly-once execution. The LangGraph version of the idea is a maintained
example in [`examples/langgraph/`](../examples/langgraph/README.md); the
application does not import it.

## Evidence

System evidence reads the selected journey back from Aurora through
`backend/db/journey_store.py`: thread, snapshot, executions, worker, session stops,
authorization, hold ID, amount and expiry. A failed readback is reported as
unavailable evidence, not as a new outcome.

Related source:

- `frontend/src/api/request.ts`: bounded browser waits, which do not cancel transactions
- `frontend/src/showcase/lib/bookingRecovery.ts`: persisted retry references; the receipt itself comes from Aurora
- `backend/routers/chat.py`, `read_hold` and `read_booking`: authenticated reconciliation reads
- `scripts/migrations/010_confirm_booking.sql`: confirmation of an unexpired catalog hold, with no supplier booking or payment
- `scripts/lost_response_proof.py` and `scripts/kill_and_resume_proof.py`: the two failure exercises and their cleanup
