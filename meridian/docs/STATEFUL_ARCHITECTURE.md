# Stateful architecture

Meridian is stateful because its agents write conversational, operational,
governance and workflow state to durable stores. It does not depend on a
long-lived database connection, or on a process staying alive, to remember
prior work.

> Statefulness lives in durable stores, not database connections or processes.

## State and transport

| State | Durable store | Access path |
| --- | --- | --- |
| Traveler profile, preferences, conversations, and interactions | AWS Aurora PostgreSQL | RDS Data API |
| Operational records, authorization bindings, and audit evidence | AWS Aurora PostgreSQL | RDS Data API |
| Managed session and semantic memory across turns, when configured | Amazon Bedrock AgentCore Memory | AgentCore APIs |
| Phase 5 workflow state: one Strands Graph snapshot per node, append-only JSONB | AWS Aurora PostgreSQL table `workflow_snapshots` | The `MeridianWorkflow` Runtime, as the `meridian_workflow` login, over the RDS Data API |
| Journey ownership, execution leases, and hold-request identity | AWS Aurora PostgreSQL | Scoped RDS Data API transactions |
| Presenter stops of a Runtime session | AWS Aurora PostgreSQL table `workflow_session_stops` | `POST /api/journeys/{journey_id}/stop-session`, in a scoped Data API transaction |
| Courtesy holds, from the Phase 4 agent and the Phase 5 workflow | AWS Aurora PostgreSQL | AgentCore Gateway tool under Cedar policy, then the `MeridianHolds` Lambda in one scoped Data API transaction; the workflow passes its saved request ID, booking ID and execution ID so the Lambda checks the worker's lease and a resumed worker replays the same booking |
| Phase 4 agent conversation | Amazon Bedrock AgentCore Memory | The Runtime's Strands session manager |
| In-turn model reasoning | AgentCore Runtime microVM | Transient by design; spans and the trace ID persist in CloudWatch |

The RDS Data API remains a connectionless, IAM-authorized HTTPS transport. It
uses database credentials stored in Secrets Manager to read and write durable
Aurora state. A Data API transaction ID keeps `SET LOCAL ROLE`, traveler GUCs,
and one read or write unit together; it is not long-lived workflow state.
Phase 4 commits its authorized read unit before calling AgentCore or Gateway,
then reauthorizes in a separate short write-and-audit unit.

MCP is orthogonal to the database transport. It defines governed tool contracts;
an MCP server can use the Data API or PostgreSQL wire protocol internally.

## Phase contract

| Phase | State and transport contract |
| --- | --- |
| SQL | Parameterized catalog reads through the Data API |
| MCP | Governed tools whose current database implementation uses the Data API |
| Retrieval | Structured, pgvector, and full-text retrieval from durable Aurora data |
| Production | Authorized, RLS-scoped Aurora memory and audit around an AgentCore Runtime turn; the agent's tools come from AgentCore Gateway, Cedar policy decides each call, and the governed hold is one scoped Data API transaction in the gateway Lambda |
| Workflow | A Strands Graph, built with `GraphBuilder`, in its own AgentCore Runtime. It saves a snapshot to Aurora after every node and places its hold through Gateway and Cedar |

## Phase 5: a Strands Graph in its own Runtime

Phase 5 is a Strands 1.57.2 Graph that runs in the `MeridianWorkflow` AgentCore
Runtime. The FastAPI backend does not run it. The backend invokes the Runtime on
a session ID derived from the traveler and thread, and relays the result. The
backend keeps one control of its own, the stop endpoint described below.

### Nodes

`graph.py` builds the Graph from seven nodes: `classify`, `search`,
`availability`, `memory_recall`, `prepare_hold`, `hold` and `synthesize`. Each
node is a deterministic step in `nodes.py`. Its result carries a JSON delta of
the state keys it changed and the spans it emitted. Folding the saved node
results rebuilds the workflow state in any process, so nothing lives on the
microVM between runs.

A review gate pauses the Graph with an interrupt where the traveler must answer
before work continues: after `search` when the request is a recovery or asks for
review, and before `prepare_hold` on every route. A resume is the answer, and
the runner counts it as consent only when the saved snapshot shows a pending or answered review or
confirmation, or a run that already got past `prepare_hold`.

### Snapshots

`SnapshotSessionManager` saves the Graph state after every node.
`AuroraSnapshotStorage` in `snapshot_storage.py` appends each save as a row of
`workflow_snapshots`. The table keeps every node boundary of a run, and the
newest row for a storage key is what a resume restores. Rows are stamped with the
traveler, execution and worker that trusted code supplies after the lease claim,
never with values read from the snapshot. A snapshot over 900,000 bytes is
refused, because the Data API refuses results over 1 MB, and the storage refuses
deletes.

### The `meridian_workflow` login

The Runtime connects to Aurora as its own login, `meridian_workflow`, created by
migration 015 and given a password by `scripts/provision_workflow_login.py`. The
role is NOBYPASSRLS and NOINHERIT, owns nothing, and reaches `meridian_app`
privileges only through `SET ROLE` inside a scoped session. The workflow no longer
runs as the cluster master role.

Each snapshot read and write opens its own transaction and pins
`app.current_traveler_id` before the statement. These RLS policies on
`workflow_snapshots` then decide what the login sees and appends:

- `workflow_snapshots_workflow_select` and `workflow_snapshots_workflow_insert`
  apply to `meridian_workflow`. Both require the row's traveler to match the
  pinned traveler and the session to be a bound journey thread.
- `workflow_snapshots_traveler_select` gives `meridian_app` read access only, so
  the System evidence view can read snapshots under the same rule.

The login has `SELECT` on the journey tables and the catalog, `SELECT` and
`INSERT` on `workflow_snapshots`, and an insert policy on
`traveler_access_audit` for the grant check.

### The lease and fenced writes

`journey_executions` holds one running execution per thread, enforced by a unique
index on `thread_id` where `status = 'running'`. The runner claims it before the
Graph starts, renews it every 10 seconds against a 60-second lease, and releases
it when the run ends. A dead worker cannot release, so its lease expires and the
next claimant marks the execution abandoned.

Snapshot writes are fenced on that row. The insert statement runs only while the
writing execution is still the thread's running one. A worker that stalled past
its lease writes nothing, and the storage raises `ExecutionLeaseLostError`
instead of appending an older state after another worker took the thread.

### The hold

The `prepare_hold` node fixes the hold's request ID and booking ID before the
`hold` node runs. The request ID is `hrq_` plus a random UUID fragment, generated
once, and the booking ID is `hold_` plus the same fragment. Both are saved in the
snapshot, so every replay presents the same key because it was saved, not
derived, and the primary key on `bookings` rejects a duplicate. The `hold` node calls the Gateway tool, so Cedar decides it
and the `MeridianHolds` Lambda checks the worker's lease inside the write
transaction. A snapshot and a business write are separate transactions. The
design makes the write idempotent and the run resumable; it does not claim
exactly-once execution.

### Resume gaps closed in the code

Two Strands 1.57.2 behaviors would otherwise break a resume:

- Strands saves the snapshot of the node that consumed the traveler's answer
  before it deactivates the interrupt state. A worker that dies in the next node
  leaves that snapshot as the latest, with every interrupt answered but the state
  still active, and a restore asks for the answers again. `ResumableStorage`
  repairs the snapshot on read, rewriting that state to the deactivated shape
  Strands saves one node later. The runner reads such a snapshot as the
  traveler's consent.
- A step that was retried can leave the Graph result marked failed even though
  its work completed. The runner treats a run as finished only when `synthesize`
  is among the completed nodes.

### The Strands pin

The pin is `strands-agents==1.57.2` in `requirements.in`, and both AgentCore
Runtimes use the same release. `tests/test_strands_pin.py` fails when the
installed version differs. The pin matters because `ResumableStorage` depends on
Strands internals: when the snapshot is saved, and the shape of its interrupt
state. Upgrade by reading those internals again, then run the live recovery
proofs.

### Stopping a session

`POST /api/journeys/{journey_id}/stop-session` stops the Runtime session for the
journey's active thread. The thread comes from the journey under RLS, never from
the request. The backend then reads the newest snapshot and execution under a
row lock and records the stop in `workflow_session_stops` as:

- `waiting`: the execution was paused, neither running nor finished, usually
  waiting for the traveler's review.
- `running`: a worker was mid-run. The stop closes its lease as abandoned, so a
  resume claims at once.
- `finished`: the saved snapshot shows the Graph completed and only the lease
  release was outstanding. The stop closes the execution as succeeded.

The record includes the last saved step. A stopped worker cannot save another
snapshot, because the insert is fenced on a running execution, and it cannot
place a hold, because the Lambda checks the lease. The next resume starts a new
microVM on the same session, claims the next attempt and restores the newest
snapshot.

Where a mid-run stop lands depends on when it is pressed. It can land before
the hold, after the hold committed but before its snapshot was saved, or after
the run finished, which is recorded as `finished`. The `--during running` proof
can miss that window and then exits 2 with "missed the window"; run it again.
Only the local kill-and-resume proof stops at an exact step.

### LangGraph

The application does not import LangGraph. The maintained LangGraph example, with
its Data API checkpoint saver and its own tests, is in
[`examples/langgraph/`](../examples/langgraph/README.md).

## Identity: five hops

After the identity release, a signed-in person is the only source of the traveler. The Amazon Cognito access token
carries a `traveler_id` claim, copied from the user's active `cognito` row in `traveler_identity_bindings` by a
pre-token-generation trigger. Each hop below checks the token itself; none trusts the one before it. Until the release,
`MERIDIAN_AGENTCORE_AUTH` defaults to `iam` and the Runtimes and the Gateway still use IAM authorizers (see
[Switch the AgentCore identity mode](OPERATIONS.md#switch-the-agentcore-identity-mode)).

| Hop | What happens | What a refusal looks like |
| --- | --- | --- |
| Browser | Authorization code with PKCE against the hosted sign-in page. The tokens live in memory and are refreshed a minute before they expire | The sign-in screen |
| Backend | FastAPI verifies signature, issuer, `token_use`, client and expiry, then takes the traveler from the claim. A request that names another traveler is refused | 401 `sign_in_required` or `token_expired`; 403 for another traveler |
| Runtimes | Both Runtimes have a JWT authorizer and allowlist `Authorization`. The traveler comes from the claim, and a payload that names someone else is refused | A coded `authorization` event |
| Gateway | The Gateway verifies the token (`CUSTOM_JWT`; the live Gateway is moved through the API, and the stack template keeps `AWS_IAM` on purpose, see [AGENTCORE_LEARNINGS.md](AGENTCORE_LEARNINGS.md#gateway-authorizer-what-the-first-window-showed)). Which layers pin the traveler depends on the design that shipped, recorded in each receipt's `design` field: `both` (an interceptor replaces `travelerId` with the token's traveler and Cedar denies a mismatch), `cedar` or `interceptor`. The measured behavior is in [AGENTCORE_LEARNINGS.md](AGENTCORE_LEARNINGS.md#gateway-identity-what-the-harness-measured) | A tool error; a deny row in `traveler_access_audit` when the Holds Lambda refused |
| AWS Aurora | The Lambda sets `app.current_traveler_id` from the argument and steps down to `meridian_app`. Row-level security filters every row to that traveler | Zero rows; a grant denial before any scope is set |

`scripts/identity_proof.py` records, for each layer, that a second signed-in user (the decoy) is refused and that
Jordan is allowed (see [Prove the decoy is refused](OPERATIONS.md#prove-the-decoy-is-refused)); it runs after the
release. One limit stays. Aurora cannot see the token behind the Data API, so the traveler setting is pinned by our
code. Having the database verify a signed scope was evaluated and not shipped; see
[SIGNED_SCOPE_EVALUATION.md](SIGNED_SCOPE_EVALUATION.md).

## Production guidance

- Apply the tracked migrations before starting the application, and run
  `scripts/provision_workflow_login.py --apply` so the Runtime has its own login.
  Do not use the cluster master role as the workflow's database role.
- Keep database transactions short. Do not hold an RLS transaction open while
  waiting for model or external service calls.
- Treat snapshots and business side effects as separate consistency domains.
  The sample persists a hold-request identity and stable booking ID across
  retries, uses worker leases, and limits compensation to the current intent.
  These controls do not turn package holds into airline ticket issuance.
- Publish only when `MeridianWorkflow` is READY. `scripts/publish.py` refuses
  otherwise.

## Verifying recovery

A Phase 5 recovery is verified when you:

1. Run a multi-node workflow and pause it after a saved step.
2. Stop the Runtime session with the stop endpoint, or kill the worker.
3. Resume with the same `thread_id`.
4. Show that execution continues from the newest row in `workflow_snapshots`.
5. Read the execution records and `workflow_session_stops` to verify that the
   replacement worker succeeded. An additional attempt alone is insufficient.
6. For the hold check, show one booking for the request, with the same booking
   ID and original 15-minute expiry before and after replacement.

## Summary

The Data API stays connectionless, but every turn reads and writes durable
state in Aurora. AgentCore Memory adds managed conversational context. When
execution becomes multi-step, the Strands Graph in the `MeridianWorkflow`
Runtime saves a snapshot to Aurora after every node. A session can be stopped,
and a new microVM resumes from the newest snapshot; the execution, snapshot and
stop records show the continuity.

These statements are not accurate descriptions of the design:

- "The Data API becomes stateful."
- "The Data API is IAM-only."
- "All five phases run entirely over the Data API."
- "RDS Proxy is always preferred."
- "A microVM keeps the workflow's state between runs."
