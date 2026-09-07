# Stateful Architecture Decision

## Session promise

**Title:** Build stateful agentic AI workflows with Aurora, MCP, and AgentCore

The architecture is stateful because agents externalize conversational,
operational, governance, and workflow state into durable stores. It does not
depend on a long-lived database connection to remember prior work.

> Statefulness lives in durable stores, not database connections.

## State and transport

| State | Durable store | Access path |
| --- | --- | --- |
| Traveler profile, preferences, conversations, and interactions | Aurora PostgreSQL | RDS Data API |
| Operational records, authorization bindings, and audit evidence | Aurora PostgreSQL | RDS Data API |
| Managed session and semantic memory across turns, when configured | Bedrock AgentCore Memory | AgentCore APIs |
| LangGraph execution position, channel values, and pending writes | Aurora PostgreSQL | `AuroraDataApiSaver` over RDS Data API, or `AsyncPostgresSaver` over pooled psycopg |
| Journey ownership, execution leases, and hold-request identity | Aurora PostgreSQL | Scoped RDS Data API transactions |
| In-turn model reasoning | Agent process | Transient by design |

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
| Production | AgentCore context plus authorized, RLS-scoped Aurora memory and audit |
| Workflow | The same domain-data paths composed by LangGraph, with durable Aurora checkpoints and persisted execution and hold records |

Phase 5 supports two checkpoint transports. Set
`LANGGRAPH_CHECKPOINT_DATA_API=true` to use the repository's `AuroraDataApiSaver`
without a direct PostgreSQL connection. A resolved checkpoint DSN takes
precedence and selects a pooled `AsyncPostgresSaver`. Both persist workflow
state in Aurora. The `/health` response and per-run evidence identify the
actual backend and whether it is durable.

## Production target

- Choose the checkpoint transport explicitly and apply the tracked migrations
  before starting the application. The live workshop uses `AuroraDataApiSaver`.
- For direct PostgreSQL checkpointing, give the worker network access to the
  private Aurora endpoint and use one bounded application-lifetime psycopg pool
  with a shared `AsyncPostgresSaver`.
- Use least-privilege checkpoint access. Do not use the cluster master role as
  the application's long-term database role.
- Set `LANGGRAPH_CHECKPOINT_REQUIRED=true`. `MemorySaver` is an in-process
  fallback and must never be represented as durable.
- RDS Proxy is an optional connection-management choice for the PostgreSQL
  transport, not a requirement of the Data API checkpoint path.
- Keep database transactions short. Do not hold an RLS transaction open while
  waiting for model or external service calls.
- Treat checkpoints and business side effects as separate consistency domains.
  The sample persists a hold-request identity and stable booking ID across
  retries, uses worker leases, and limits compensation to the current intent.
  These controls do not turn package holds into airline ticket issuance.

## Live proof contract

The strongest Phase 5 proof is:

1. Run a multi-node workflow with a durable Aurora checkpoint backend.
2. Pause after a committed worker-node checkpoint.
3. Stop and restart the workflow worker.
4. Resume with the same `thread_id`.
5. Show that execution continues from Aurora's checkpoint tables:
   `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, and
   `checkpoint_migrations`.
6. Read the execution records and persisted resume receipt to verify that the
   replacement worker succeeded. An additional attempt alone is insufficient.
7. For the hold demonstration, show one booking for the request, with the same
   booking ID and original 15-minute expiry before and after replacement.

If the trace says `MemorySaver`, describe the run as in-process only. It does not
satisfy the live Aurora-checkpoint proof.

## Presenter wording

> The Data API remains connectionless, but every turn reads and writes durable
> state in Aurora. AgentCore Memory can add managed conversational context.
> When execution becomes multi-step, LangGraph externalizes workflow state
> into Aurora. This demo uses the Data API checkpoint saver; a pooled PostgreSQL
> saver is also supported. We can terminate the worker, resume from the saved
> checkpoint, and read back the execution and hold records to prove continuity.

Avoid these claims:

- "The Data API becomes stateful."
- "The Data API is IAM-only."
- "All five phases run entirely over the Data API."
- "RDS Proxy is always preferred."
- "MemorySaver proves durable workflow recovery."
